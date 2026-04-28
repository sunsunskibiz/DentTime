#!/usr/bin/env bash
# Run from the DentTime project root:
#   bash scripts/run_critical_alert_demo.sh [--f1-total 130] [--under-total 40]
set -euo pipefail

API_BASE="http://localhost:8001"
F1_CRITICAL_TOTAL=130
F1_ACTUAL_DURATION=45
UNDER_EST_CRITICAL_TOTAL=40
UNDER_EST_ACTUAL_DURATION=180
MAX_PARALLEL=20
SKIP_ACTUAL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-base=*)        API_BASE="${1#*=}" ;;
        --api-base)          API_BASE="$2"; shift ;;
        --f1-total=*)        F1_CRITICAL_TOTAL="${1#*=}" ;;
        --f1-total)          F1_CRITICAL_TOTAL="$2"; shift ;;
        --f1-actual=*)       F1_ACTUAL_DURATION="${1#*=}" ;;
        --f1-actual)         F1_ACTUAL_DURATION="$2"; shift ;;
        --under-total=*)     UNDER_EST_CRITICAL_TOTAL="${1#*=}" ;;
        --under-total)       UNDER_EST_CRITICAL_TOTAL="$2"; shift ;;
        --under-actual=*)    UNDER_EST_ACTUAL_DURATION="${1#*=}" ;;
        --under-actual)      UNDER_EST_ACTUAL_DURATION="$2"; shift ;;
        --max-parallel=*)    MAX_PARALLEL="${1#*=}" ;;
        --max-parallel)      MAX_PARALLEL="$2"; shift ;;
        --skip-actual)       SKIP_ACTUAL=true ;;
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
    local f1="$1" f1_baseline="$2" under="$3" under_baseline="$4" missing="$5"
    python3 -c "
f1, f1_base = $f1, $f1_baseline
under, under_base = $under, $under_baseline
missing = $missing
f1_thr = f1_base - 0.05
under_thr = under_base + 0.05
print()
print('Alert expectation from Prometheus rules:')
print(f'DentTimeMacroF1Drop        : current={f1:.4f}, threshold < {f1_thr:.4f}, expected={\"FIRING\" if f1 < f1_thr else \"not firing yet\"}')
print(f'DentTimeUnderEstimationHigh: current={under:.4f}, threshold > {under_thr:.4f}, expected={\"FIRING\" if under > under_thr else \"not firing yet\"}')
print(f'DentTimeMissingRateHigh    : current={missing:.4f}, threshold > 0.1000, expected={\"FIRING\" if missing > 0.10 else \"not firing yet\"}')
"
}

# ── per-request worker (runs in a background subshell) ───────────────────────
_send_one() {
    local i="$1" total="$2" scenario_name="$3" actual_duration="$4" offset="$5" clinic_id="$6" results_dir="$7"

    local selected_dt; selected_dt=$(add_minutes "$offset")
    local request_time; request_time=$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())")

    local payload="{\"treatmentSymptoms\":\"UNSEEN_CRITICAL_DEMO_TREATMENT_${scenario_name}_${i}\",\
\"toothNumbers\":\"11,12,13,14,15,16,17,18,21,22,23,24,25,26,27,28,31,32,33,34,35,36,37,38,41,42,43,44,45,46,47,48\",\
\"surfaces\":\"M,O,D,B,L\",\
\"totalAmount\":99999,\
\"selectedDateTime\":\"${selected_dt}\",\
\"clinicId\":\"${clinic_id}\",\
\"request_time\":\"${request_time}\"}"

    if result=$(json_post "$API_BASE/predict" "$payload" 2>/dev/null); then
        predicted=$(parse_field "$result" "['predicted_duration_class']")
        request_id=$(parse_field "$result" "['request_id']")
        printf "[%d/%d] OK  predicted=%s minutes, request_id=%s\n" "$i" "$total" "$predicted" "$request_id"
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
    local scenario_name="$1" total="$2" actual_duration="$3" minute_offset_base="$4" clinic_id="$5"
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
        _send_one "$i" "$total" "$scenario_name" "$actual_duration" \
            $(( minute_offset_base + i )) "$clinic_id" "$results_dir" &
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
write_step "DentTime Critical Alert Demo"
echo "API Base                      : $API_BASE"
echo "F1 critical batch size        : $F1_CRITICAL_TOTAL"
echo "F1 critical actual duration   : ${F1_ACTUAL_DURATION} minutes"
echo "Under-estimation batch size   : $UNDER_EST_CRITICAL_TOTAL"
echo "Under-estimation actual label : ${UNDER_EST_ACTUAL_DURATION} minutes"
echo "Max parallel workers          : $MAX_PARALLEL"
echo "Send /actual labels           : $( [ "$SKIP_ACTUAL" = true ] && echo false || echo true )"

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
echo "Before Macro F1           : ${before_f1:-N/A}"
echo "Before Macro F1 baseline  : ${before_f1_baseline:-N/A}"
echo "Before under-estimation   : ${before_under:-N/A}"
echo "Before under baseline     : ${before_under_baseline:-N/A}"
echo "Before missing rate       : ${before_missing:-N/A}"

# ── Batches ───────────────────────────────────────────────────────────────────
send_scenario_batch "MACRO_F1_CRITICAL"  "$F1_CRITICAL_TOTAL"       "$F1_ACTUAL_DURATION"       0     "CLINIC_MACRO_F1_CRITICAL"
send_scenario_batch "UNDER_EST_CRITICAL" "$UNDER_EST_CRITICAL_TOTAL" "$UNDER_EST_ACTUAL_DURATION" 10000 "CLINIC_UNDER_EST_CRITICAL"

# ── Wait for metrics pipeline ─────────────────────────────────────────────────
write_step "Waiting for metrics_updater and Prometheus scrape"
echo "Sleeping 45 s — metrics_updater refreshes state.json every ~15 s."
sleep 45

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
echo "After Macro F1            : ${after_f1:-N/A}"
echo "After Macro F1 baseline   : ${after_f1_baseline:-N/A}"
echo "After MAE minutes         : ${after_mae:-N/A}"
echo "After under-estimation    : ${after_under:-N/A}"
echo "After under baseline      : ${after_under_baseline:-N/A}"
echo "After missing rate        : ${after_missing:-N/A}"

if [[ -n "${after_f1:-}" && -n "${after_f1_baseline:-}" && -n "${after_under:-}" && -n "${after_under_baseline:-}" && -n "${after_missing:-}" ]]; then
    check_alert_expectation "$after_f1" "$after_f1_baseline" "$after_under" "$after_under_baseline" "$after_missing"
fi

write_step "Open these pages for the teacher demo"
echo "Grafana dashboard : http://localhost:3000/d/denttime-prometheus/denttime-monitoring-dashboard?orgId=1"
echo "Prometheus alerts : http://localhost:9090/alerts"
echo "Backend metrics   : http://localhost:8000/metrics"
echo ""
echo "If Prometheus still shows Pending, wait ~1 minute (alert rules use 'for: 1m')."
