#!/usr/bin/env bash
# Run from the DentTime project root:
#   bash scripts/run_data_diff_demo.sh [--total 80] [--delay-ms 120] [--skip-actual] [--actual-duration 105]
set -euo pipefail

API_BASE="http://localhost:8001"
TOTAL=80
DELAY_MS=120
SKIP_ACTUAL=false
ACTUAL_DURATION=105

while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-base=*)    API_BASE="${1#*=}" ;;
        --api-base)      API_BASE="$2"; shift ;;
        --total=*)       TOTAL="${1#*=}" ;;
        --total)         TOTAL="$2"; shift ;;
        --delay-ms=*)    DELAY_MS="${1#*=}" ;;
        --delay-ms)      DELAY_MS="$2"; shift ;;
        --skip-actual)   SKIP_ACTUAL=true ;;
        --actual-duration=*) ACTUAL_DURATION="${1#*=}" ;;
        --actual-duration)   ACTUAL_DURATION="$2"; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
    shift
done

DELAY_SEC=$(python3 -c "print($DELAY_MS / 1000)")

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

# ── Banner ────────────────────────────────────────────────────────────────────
write_step "DentTime Data Diff Batch Request Demo"
echo "API Base              : $API_BASE"
echo "Total drift requests  : $TOTAL"
echo "Delay between requests: ${DELAY_MS} ms"
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
echo "Before logged predictions : $(get_metric_value "$before" denttime_logged_predictions_total)"
echo "Before missing rate       : $(get_metric_value "$before" denttime_input_missing_rate)"
echo "Before under-estimation   : $(get_metric_value "$before" denttime_underestimation_rate)"

# ── Send shifted requests ─────────────────────────────────────────────────────
write_step "Sending intentionally shifted requests"
echo "1) Unseen treatment names  → UNKNOWN treatment class"
echo "2) totalAmount=99999       → amount distribution drift"
echo "3) All 32 teeth, 5 surfaces → feature distribution drift"
echo "4) Missing doctorId/notes  → input missing rate rises"
echo "5) actual_duration=${ACTUAL_DURATION} → under-estimation / F1 demo"
echo ""

success=0; failed=0

for (( i=1; i<=TOTAL; i++ )); do
    selected_dt=$(add_minutes "$i")
    request_time=$(python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).isoformat())")

    payload="{\"treatmentSymptoms\":\"UNSEEN_DATA_DIFF_TREATMENT_${i}\",\
\"toothNumbers\":\"11,12,13,14,15,16,17,18,21,22,23,24,25,26,27,28,31,32,33,34,35,36,37,38,41,42,43,44,45,46,47,48\",\
\"surfaces\":\"M,O,D,B,L\",\
\"totalAmount\":99999,\
\"selectedDateTime\":\"${selected_dt}\",\
\"clinicId\":\"CLINIC_DATA_DIFF_UNKNOWN\",\
\"request_time\":\"${request_time}\"}"

    if result=$(json_post "$API_BASE/predict" "$payload" 2>/dev/null); then
        success=$(( success + 1 ))
        predicted=$(parse_field "$result" "['predicted_duration_class']")
        unit=$(parse_field "$result" ".get('unit','minutes')")
        request_id=$(parse_field "$result" "['request_id']")
        printf "[%d/%d] OK  predicted=%s %s, request_id=%s\n" "$i" "$TOTAL" "$predicted" "$unit" "$request_id"

        if [ "$SKIP_ACTUAL" = false ]; then
            actual_payload="{\"request_id\":\"${request_id}\",\"actual_duration\":${ACTUAL_DURATION},\"unit\":\"minutes\"}"
            if actual_result=$(json_post "$API_BASE/actual" "$actual_payload" 2>/dev/null); then
                actual_status=$(parse_field "$actual_result" ".get('status','ok')")
                printf "        actual logged=%s minutes, status=%s\n" "$ACTUAL_DURATION" "$actual_status"
            fi
        fi
    else
        failed=$(( failed + 1 ))
        printf "[%d/%d] FAILED\n" "$i" "$TOTAL"
    fi

    sleep "$DELAY_SEC"
done

# ── Wait for metrics pipeline ─────────────────────────────────────────────────
write_step "Waiting for metrics_updater and Prometheus scrape"
echo "Sleeping 35 s — metrics_updater refreshes state.json every ~15 s."
sleep 35

after=$(get_metrics_text)

# ── Summary ───────────────────────────────────────────────────────────────────
write_step "Summary"
echo "Requests succeeded        : $success"
echo "Requests failed           : $failed"
echo "After logged predictions  : $(get_metric_value "$after" denttime_logged_predictions_total)"
echo "After missing rate        : $(get_metric_value "$after" denttime_input_missing_rate)"
echo "After under-estimation    : $(get_metric_value "$after" denttime_underestimation_rate)"
echo "After macro F1            : $(get_metric_value "$after" denttime_macro_f1)"
echo "After MAE minutes         : $(get_metric_value "$after" denttime_mae_minutes)"

write_step "Important PSI metrics"
features=(
    treatment_class total_amount tooth_count surface_count
    appt_day_of_week appt_hour_bucket has_dentist_id
    clinic_median_duration clinic_pct_long
)
for feature in "${features[@]}"; do
    value=$(get_psi_value "$after" "$feature")
    if [ -n "$value" ]; then
        status=$(python3 -c "print('DRIFT' if float('${value}') > 0.25 else 'OK')")
        printf "%-24s PSI=%8.4f  %s\n" "$feature" "$value" "$status"
    else
        printf "%-24s PSI=not found yet\n" "$feature"
    fi
done

write_step "Open these pages for the teacher demo"
echo "Grafana dashboard : http://localhost:3000/d/denttime-prometheus/denttime-monitoring-dashboard?orgId=1"
echo "Prometheus alerts : http://localhost:9090/alerts"
echo "Backend metrics   : http://localhost:8000/metrics"
echo ""
echo "Data Diff happens because this script sends live input data whose feature"
echo "distribution is intentionally different from reference_features.parquet."
echo "The monitoring job computes PSI, writes state.json, /metrics exposes"
echo "denttime_feature_psi, and Prometheus/Grafana alert when PSI > 0.25."
