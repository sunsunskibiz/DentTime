#!/usr/bin/env bash
# Run from anywhere:
#   bash scripts/run_data_diff_demo.sh [--total 80] [--max-parallel 20] [--skip-actual] [--actual-duration 105]
set -euo pipefail

API_BASE="http://localhost:8001"
TOTAL=80
MAX_PARALLEL=20
SKIP_ACTUAL=false
ACTUAL_DURATION=105

while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-base=*)        API_BASE="${1#*=}" ;;
        --api-base)          API_BASE="$2"; shift ;;
        --total=*)           TOTAL="${1#*=}" ;;
        --total)             TOTAL="$2"; shift ;;
        --max-parallel=*)    MAX_PARALLEL="${1#*=}" ;;
        --max-parallel)      MAX_PARALLEL="$2"; shift ;;
        --skip-actual)       SKIP_ACTUAL=true ;;
        --actual-duration=*) ACTUAL_DURATION="${1#*=}" ;;
        --actual-duration)   ACTUAL_DURATION="$2"; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
    shift
done

write_step() { echo ""; echo "=== $1 ==="; }

get_metrics_text() {
    curl -sf "$API_BASE/metrics"
}

get_metric_value() {
    local text="$1" name="$2"
    echo "$text" | grep -E "^${name} " | head -1 | awk '{print $NF}'
}

get_psi_value() {
    local text="$1" feature="$2"
    echo "$text" | grep -F "denttime_feature_psi{feature=\"${feature}\"}" | head -1 | awk '{print $NF}'
}

# Pre-compute a selected_dt string for a given minute offset.
# Called ONCE per request in the main loop (outside background subshells).
add_minutes() {
    python3 -c "
from datetime import datetime, timedelta
print((datetime(2026,4,26,2,30,0) + timedelta(minutes=$1)).strftime('%Y-%m-%dT%H:%M:%S'))
"
}

json_post() {
    curl -sf -X POST -H "Content-Type: application/json; charset=utf-8" -d "$2" "$1"
}

parse_field() {
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d$2)" <<< "$1"
}

check_alert_expectation() {
    local psi_max="$1" missing="$2" f1="$3" f1_baseline="$4" under="$5" under_baseline="$6"
    python3 -c "
psi_max  = $psi_max
missing  = $missing
f1       = $f1
f1_base  = $f1_baseline
under    = $under
under_base = $under_baseline

f1_thr    = f1_base - 0.05
under_thr = under_base + 0.05

print()
print('Alert expectation from Prometheus rules:')
print(f'FeatureDriftHigh     : max PSI={psi_max:.4f}, threshold > 0.2500, expected={\"FIRING\" if psi_max > 0.25 else \"not firing yet\"}')
print(f'MissingRateHigh      : current={missing:.4f}, threshold > 0.1000, expected={\"FIRING\" if missing > 0.10 else \"not firing yet\"}')
print(f'DentTimeMacroF1Drop  : current={f1:.4f},  threshold < {f1_thr:.4f}, expected={\"FIRING\" if f1 < f1_thr else \"not firing yet\"}')
print(f'UnderEstimationHigh  : current={under:.4f}, threshold > {under_thr:.4f}, expected={\"FIRING\" if under > under_thr else \"not firing yet\"}')
"
}

# ── per-request worker (runs in a background subshell) ───────────────────────
# minute_offset is a plain integer; add_minutes + request_time are computed
# inside the subshell — identical to run_critical_alert_demo.sh so up to
# MAX_PARALLEL workers truly run in parallel.
_send_one() {
    local i="$1" total="$2" actual_duration="$3" minute_offset="$4" results_dir="$5"

    local selected_dt; selected_dt=$(add_minutes "$minute_offset")
    local request_time; request_time=$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())")

    local payload="{\"treatmentSymptoms\":\"UNSEEN_DATA_DIFF_TREATMENT_${i}\",\
\"toothNumbers\":\"11,12,13,14,15,16,17,18,21,22,23,24,25,26,27,28,31,32,33,34,35,36,37,38,41,42,43,44,45,46,47,48\",\
\"surfaces\":\"M,O,D,B,L\",\
\"totalAmount\":99999,\
\"selectedDateTime\":\"${selected_dt}\",\
\"clinicId\":\"CLINIC_DATA_DIFF_UNKNOWN\",\
\"request_time\":\"${request_time}\"}"

    if result=$(json_post "$API_BASE/predict" "$payload" 2>/dev/null); then
        predicted=$(parse_field "$result" "['predicted_duration_class']")
        unit=$(parse_field "$result" ".get('unit','minutes')")
        request_id=$(parse_field "$result" "['request_id']")
        printf "[%d/%d] OK  predicted=%s %s, request_id=%s\n" "$i" "$total" "$predicted" "$unit" "$request_id"
        touch "${results_dir}/ok_${i}"

        if [ "$SKIP_ACTUAL" = false ]; then
            local actual_payload="{\"request_id\":\"${request_id}\",\"actual_duration\":${actual_duration},\"unit\":\"minutes\"}"
            if actual_result=$(json_post "$API_BASE/actual" "$actual_payload" 2>/dev/null); then
                actual_status=$(parse_field "$actual_result" ".get('status','ok')")
                printf "        actual logged=%s minutes, status=%s\n" "$actual_duration" "$actual_status"
            fi
        fi
    else
        printf "[%d/%d] FAILED\n" "$i" "$total"
        touch "${results_dir}/fail_${i}"
    fi
}

# ── scenario batch (parallel) ─────────────────────────────────────────────────
send_scenario_batch() {
    local scenario_name="$1" total="$2" actual_duration="$3" minute_offset_base="$4"
    local results_dir; results_dir=$(mktemp -d)

    write_step "$scenario_name"
    echo "Requests to send       : $total"
    echo "Actual duration labels : ${actual_duration} minutes"
    echo "Max parallel           : $MAX_PARALLEL"
    echo "Purpose                : create controlled monitoring degradation for classroom demo"
    echo ""

    for (( i=1; i<=total; i++ )); do
        # Semaphore: wait until a worker slot is free before launching the next job.
        while (( $(jobs -rp | wc -l) >= MAX_PARALLEL )); do
            sleep 0.05
        done
        _send_one "$i" "$total" "$actual_duration" $(( minute_offset_base + i )) "$results_dir" &
    done
    wait  # collect all remaining background jobs

    local success; success=$(find "$results_dir" -name "ok_*"   2>/dev/null | wc -l | tr -d ' ')
    local failed;  failed=$(find  "$results_dir" -name "fail_*" 2>/dev/null | wc -l | tr -d ' ')
    rm -rf "$results_dir"

    echo ""
    echo "Batch succeeded: $success"
    echo "Batch failed   : $failed"
}

# ── Banner ────────────────────────────────────────────────────────────────────
write_step "DentTime Data Diff Batch Request Demo"
echo "API Base              : $API_BASE"
echo "Total drift requests  : $TOTAL"
echo "Max parallel workers  : $MAX_PARALLEL"
echo "Send /actual labels   : $( [ "$SKIP_ACTUAL" = true ] && echo false || echo true )"
echo "Actual duration label : ${ACTUAL_DURATION} minutes"

# ── Health check ─────────────────────────────────────────────────────────────
write_step "Checking API"
root=$(curl -sf "$API_BASE/" 2>/dev/null) || {
    echo "ERROR: Cannot reach API at $API_BASE — is 'make up-serve' running?" >&2
    exit 1
}
echo "API status: $(parse_field "$root" "['message']")"

# ── Baseline metrics ──────────────────────────────────────────────────────────
before=$(get_metrics_text)
before_count=$(get_metric_value "$before" denttime_logged_predictions_total)
before_f1=$(get_metric_value "$before" denttime_macro_f1)
before_f1_baseline=$(get_metric_value "$before" denttime_macro_f1_baseline)
before_under=$(get_metric_value "$before" denttime_underestimation_rate)
before_under_baseline=$(get_metric_value "$before" denttime_underestimation_rate_baseline)
before_missing=$(get_metric_value "$before" denttime_input_missing_rate)

echo "Before logged predictions : ${before_count:-N/A}"
echo "Before missing rate       : ${before_missing:-N/A}"
echo "Before under-estimation   : ${before_under:-N/A}"
echo "Before Macro F1           : ${before_f1:-N/A}"

# ── Send shifted requests (parallel) ─────────────────────────────────────────
write_step "Sending intentionally shifted requests"
echo "1) Unseen treatment names   → UNKNOWN treatment class"
echo "2) totalAmount=99999        → amount distribution drift"
echo "3) All 32 teeth, 5 surfaces → feature distribution drift"
echo "4) Missing doctorId/notes   → input missing rate rises"
echo "5) actual_duration=${ACTUAL_DURATION} → under-estimation / F1 demo"

send_scenario_batch "DATA_DIFF_BATCH" "$TOTAL" "$ACTUAL_DURATION" 0

# ── Wait for metrics pipeline ─────────────────────────────────────────────────
write_step "Waiting for metrics_updater and Prometheus scrape"
echo "Sleeping 35 s — metrics_updater refreshes state.json every ~15 s."
sleep 35

after=$(get_metrics_text)
after_count=$(get_metric_value "$after" denttime_logged_predictions_total)
after_f1=$(get_metric_value "$after" denttime_macro_f1)
after_f1_baseline=$(get_metric_value "$after" denttime_macro_f1_baseline)
after_under=$(get_metric_value "$after" denttime_underestimation_rate)
after_under_baseline=$(get_metric_value "$after" denttime_underestimation_rate_baseline)
after_missing=$(get_metric_value "$after" denttime_input_missing_rate)
after_mae=$(get_metric_value "$after" denttime_mae_minutes)

# ── Summary ───────────────────────────────────────────────────────────────────
write_step "Summary"
echo "Before logged predictions : ${before_count:-N/A}"
echo "After logged predictions  : ${after_count:-N/A}"
echo "After missing rate        : ${after_missing:-N/A}"
echo "After under-estimation    : ${after_under:-N/A}"
echo "After Macro F1            : ${after_f1:-N/A}"
echo "After Macro F1 baseline   : ${after_f1_baseline:-N/A}"
echo "After MAE minutes         : ${after_mae:-N/A}"

write_step "Important PSI metrics"
features=(
    treatment_class total_amount tooth_count surface_count
    appt_day_of_week appt_hour_bucket has_dentist_id
    clinic_median_duration clinic_pct_long
)
psi_max=0
for feature in "${features[@]}"; do
    value=$(get_psi_value "$after" "$feature")
    if [ -n "$value" ]; then
        status=$(python3 -c "print('DRIFT' if float('${value}') > 0.25 else 'OK')")
        printf "%-24s PSI=%8.4f  %s\n" "$feature" "$value" "$status"
        psi_max=$(python3 -c "print(max(${psi_max}, ${value}))")
    else
        printf "%-24s PSI=not found yet\n" "$feature"
    fi
done

if [[ -n "${after_f1:-}" && -n "${after_f1_baseline:-}" && -n "${after_under:-}" && -n "${after_under_baseline:-}" && -n "${after_missing:-}" ]]; then
    check_alert_expectation "$psi_max" "$after_missing" "$after_f1" "$after_f1_baseline" "$after_under" "$after_under_baseline"
fi

write_step "Open these pages for the teacher demo"
echo "Grafana dashboard : http://localhost:3000/d/denttime-prometheus/denttime-monitoring-dashboard?orgId=1"
echo "Prometheus alerts : http://localhost:9090/alerts"
echo "Backend metrics   : http://localhost:8000/metrics"
echo ""
echo "Data Diff happens because this script sends live input data whose feature"
echo "distribution is intentionally different from reference_features.parquet."
echo "The monitoring job computes PSI, writes state.json, /metrics exposes"
echo "denttime_feature_psi, and Prometheus/Grafana alert when PSI > 0.25."
echo ""
echo "If Prometheus still shows Pending, wait ~1 minute (alert rules use 'for: 1m')."
