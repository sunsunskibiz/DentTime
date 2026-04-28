#!/usr/bin/env bash
# Reset all Prometheus alerts to green by clearing demo prediction data.
# Can be run from anywhere:
#   bash scripts/reset_alerts.sh
#   ./reset_alerts.sh         (from scripts/)
set -euo pipefail

# Always resolve paths relative to the project root (one level up from scripts/).
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

API_BASE="http://localhost:8001"
DB_PATH="data/denttime.db"
STATE_PATH="monitoring/state.json"
BASELINE_PATH="artifacts/baseline_metrics.json"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --api-base=*) API_BASE="${1#*=}" ;;
        --api-base)   API_BASE="$2"; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
    shift
done

write_step() { echo ""; echo "=== $1 ==="; }

get_metrics_text() { curl -sf "$API_BASE/metrics"; }
get_val() { local txt="$1" name="$2"; echo "$txt" | grep -E "^${name}[{ ]" | head -1 | awk '{print $NF}'; }
get_psi_max() { local txt="$1"; echo "$txt" | grep "^denttime_feature_psi{" | awk '{print $NF}' | sort -n | tail -1; }

# ── 1. Read baseline values ────────────────────────────────────────────────────
write_step "Reading baseline metrics"
if [[ ! -f "$BASELINE_PATH" ]]; then
    echo "ERROR: $BASELINE_PATH not found — is the model artifact present?" >&2
    exit 1
fi
read -r BASELINE_F1 BASELINE_UNDER < <(python3 -c "
import json
b = json.load(open('$BASELINE_PATH'))
print(b['macro_f1'], b.get('under_estimation_rate', b.get('underestimation_rate', 0.0961)))
")
echo "Baseline macro F1         : $BASELINE_F1"
echo "Baseline under-est rate   : $BASELINE_UNDER"

# ── 2. Clear predictions table ─────────────────────────────────────────────────
write_step "Clearing predictions table in SQLite"
if [[ ! -f "$DB_PATH" ]]; then
    echo "WARNING: $DB_PATH not found — nothing to clear"
else
    ROW_COUNT=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DB_PATH')
count = conn.execute('SELECT COUNT(*) FROM predictions').fetchone()[0]
conn.execute('DELETE FROM predictions')
conn.commit()
conn.close()
print(count)
")
    echo "Deleted $ROW_COUNT prediction rows"
fi

# ── 3. Write clean state.json ──────────────────────────────────────────────────
write_step "Writing clean monitoring/state.json"
python3 - <<'PYEOF'
import json, pathlib

FEATURE_COLUMNS = [
    "treatment_class", "composite_treatment_flag", "has_tooth_no", "tooth_count",
    "is_area_treatment", "surface_count", "total_amount", "has_notes",
    "appt_day_of_week", "appt_hour_bucket", "is_first_case", "has_dentist_id",
    "appointment_rank_in_day", "clinic_median_duration", "clinic_pct_long",
    "doctor_median_duration", "doctor_pct_long",
]

import os, sys
baseline_path = os.environ.get("BASELINE_PATH", "artifacts/baseline_metrics.json")
state_path    = os.environ.get("STATE_PATH",    "monitoring/state.json")

b = json.load(open(baseline_path))
baseline_f1    = b["macro_f1"]
baseline_under = b.get("under_estimation_rate", b.get("underestimation_rate", 0.0961))
baseline_mae   = b.get("mae_minutes", 8.42)

# All PSI values set to 0.0 (below the 0.25 firing threshold).
# macro_f1 / under_rate set to baseline so they are exactly at the healthy baseline.
state = {
    "feature_psi":                         {col: 0.0 for col in FEATURE_COLUMNS},
    "prediction_ratio":                    {},
    "input_missing_rate":                  0.0,
    "treatment_unknown_rate":              0.0,
    "treatment_unknown_rate_baseline":     b.get("treatment_unknown_rate_baseline", 0.0),
    "appt_hour_bucket_sentinel_rate":      0.0,
    "appt_hour_bucket_sentinel_rate_baseline": 0.0,
    "macro_f1":                            baseline_f1,
    "mae_minutes":                         baseline_mae,
    "under_estimation_rate":               baseline_under,
}

pathlib.Path(state_path).write_text(json.dumps(state, indent=2), encoding="utf-8")
print(f"Done — PSI=0.0, macro_f1={baseline_f1}, under_rate={baseline_under}, missing_rate=0.0")
PYEOF

# ── 4. Force metric gauge flush via /metrics scrape ───────────────────────────
write_step "Flushing Prometheus gauges via backend /metrics"
if curl -sf "$API_BASE/metrics" > /dev/null; then
    echo "OK — backend gauges updated from new state.json"
else
    echo "WARNING: Cannot reach $API_BASE/metrics — is 'make up-serve' running?"
    echo "         Gauges will update on next scrape once the stack is started."
fi

# ── 5. Wait for metrics_updater refresh cycle then verify ─────────────────────
write_step "Waiting 30 s for metrics_updater refresh cycle"
sleep 30

write_step "Verification"
METRICS=$(get_metrics_text 2>/dev/null) || { echo "Cannot reach $API_BASE/metrics"; exit 1; }

MAX_PSI=$(get_psi_max "$METRICS")
MISSING=$(get_val "$METRICS" denttime_input_missing_rate)
F1=$(get_val      "$METRICS" denttime_macro_f1)
F1_BASE=$(get_val "$METRICS" denttime_macro_f1_baseline)
UNDER=$(get_val   "$METRICS" denttime_underestimation_rate)
UNDER_BASE=$(get_val "$METRICS" denttime_underestimation_rate_baseline)

echo "Max feature PSI    : ${MAX_PSI:-N/A}"
echo "Input missing rate : ${MISSING:-N/A}  (threshold 0.10)"
echo "Macro F1           : ${F1:-N/A}  (baseline ${F1_BASE:-N/A})"
echo "Under-est rate     : ${UNDER:-N/A}  (baseline ${UNDER_BASE:-N/A})"

python3 - <<PYEOF
max_psi    = ${MAX_PSI:-0}
missing    = ${MISSING:-0}
f1         = ${F1:-0}
f1_base    = ${F1_BASE:-0}
under      = ${UNDER:-0}
under_base = ${UNDER_BASE:-0}

issues = []
if max_psi > 0.25:         issues.append(f"FeatureDriftHigh       : max PSI {max_psi:.4f} > 0.25")
if missing > 0.10:         issues.append(f"MissingRateHigh        : {missing:.4f} > 0.10")
if f1 < f1_base - 0.05:   issues.append(f"MacroF1Drop            : {f1:.4f} < {f1_base - 0.05:.4f}")
if under > under_base+0.05:issues.append(f"UnderEstimationHigh    : {under:.4f} > {under_base + 0.05:.4f}")

if issues:
    print("\nStill outside thresholds:")
    for i in issues:
        print("  STILL FIRING:", i)
else:
    print("\nAll metrics within thresholds.")
    print("Prometheus alerts will resolve within ~1 minute (alert rules use 'for: 1m').")
PYEOF

write_step "Done"
echo "Prometheus alerts : http://localhost:9090/alerts"
echo "Grafana dashboard : http://localhost:3000/d/denttime-prometheus/denttime-monitoring-dashboard?orgId=1"
echo ""
echo "If alerts still show Pending after running, wait 1 more minute — Prometheus"
echo "requires the condition to clear for the full 'for: 1m' window before resolving."
