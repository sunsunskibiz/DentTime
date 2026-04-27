param(
    [string]$ApiBase = "http://localhost:8000",

    # This stage is the key for making DentTimeMacroF1Drop become FIRING.
    # It creates labeled examples whose actual class is intentionally different
    # from the model's current prediction pattern. This lowers rolling Macro F1.
    [int]$F1CriticalTotal = 130,
    [int]$F1ActualDuration = 45,

    # This stage keeps DentTimeUnderEstimationHigh FIRING as a critical alert.
    # 180 minutes is intentionally larger than all normal DentTime classes,
    # so predictions such as 15/30/60/105 become under-estimations.
    [int]$UnderEstCriticalTotal = 40,
    [int]$UnderEstActualDuration = 180,

    [int]$DelayMs = 120,
    [switch]$SkipActual
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
}

function Invoke-JsonPost {
    param(
        [Parameter(Mandatory=$true)][string]$Url,
        [Parameter(Mandatory=$true)]$Body
    )

    $json = $Body | ConvertTo-Json -Depth 20 -Compress
    return Invoke-RestMethod `
        -Uri $Url `
        -Method Post `
        -ContentType "application/json; charset=utf-8" `
        -Body $json
}

function Get-MetricsText {
    return (Invoke-WebRequest -Uri "$ApiBase/metrics" -UseBasicParsing).Content
}

function Get-MetricValue {
    param(
        [Parameter(Mandatory=$true)][string]$MetricsText,
        [Parameter(Mandatory=$true)][string]$MetricName
    )

    $line = ($MetricsText -split "`n" | Where-Object {
        $_ -match "^$([regex]::Escape($MetricName))\s+[-+]?[0-9]*\.?[0-9]+"
    } | Select-Object -First 1)

    if (-not $line) { return $null }
    return [double](($line -split "\s+")[-1])
}

function Get-AlertExpectation {
    param(
        [Parameter(Mandatory=$true)][double]$MacroF1,
        [Parameter(Mandatory=$true)][double]$MacroF1Baseline,
        [Parameter(Mandatory=$true)][double]$UnderRate,
        [Parameter(Mandatory=$true)][double]$UnderRateBaseline,
        [Parameter(Mandatory=$true)][double]$MissingRate
    )

    $f1Threshold = $MacroF1Baseline - 0.05
    $underThreshold = $UnderRateBaseline + 0.05

    Write-Host ""
    Write-Host "Alert expectation from Prometheus rules:" -ForegroundColor Yellow
    Write-Host ("DentTimeMacroF1Drop        : current={0:N4}, threshold < {1:N4}, expected={2}" -f `
        $MacroF1, $f1Threshold, $(if ($MacroF1 -lt $f1Threshold) { "FIRING" } else { "not firing yet" }))
    Write-Host ("DentTimeUnderEstimationHigh: current={0:N4}, threshold > {1:N4}, expected={2}" -f `
        $UnderRate, $underThreshold, $(if ($UnderRate -gt $underThreshold) { "FIRING" } else { "not firing yet" }))
    Write-Host ("DentTimeMissingRateHigh    : current={0:N4}, threshold > 0.1000, expected={1}" -f `
        $MissingRate, $(if ($MissingRate -gt 0.10) { "FIRING" } else { "not firing yet" }))
}

function Send-ScenarioBatch {
    param(
        [Parameter(Mandatory=$true)][string]$ScenarioName,
        [Parameter(Mandatory=$true)][int]$Total,
        [Parameter(Mandatory=$true)][int]$ActualDuration,
        [Parameter(Mandatory=$true)][int]$MinuteOffsetBase,
        [Parameter(Mandatory=$true)][string]$ClinicId
    )

    Write-Step $ScenarioName
    Write-Host "Requests to send       : $Total"
    Write-Host "Actual duration labels : $ActualDuration minutes"
    Write-Host "Purpose                : create controlled monitoring degradation for classroom demo"
    Write-Host ""

    $success = 0
    $failed = 0

    for ($i = 1; $i -le $Total; $i++) {
        # Fixed Sunday night appointment + high amount + many teeth/surfaces:
        # this intentionally produces large PSI / Data Diff.
        $selectedDateTime = ([datetime]"2026-04-26T02:30:00").AddMinutes($MinuteOffsetBase + $i).ToString("yyyy-MM-ddTHH:mm:ss")

        # Omit doctorId and notes on purpose.
        # compute_input_missing_rate watches doctorId and notes,
        # so missing-rate alert should rise.
        $payload = [ordered]@{
            treatmentSymptoms = "UNSEEN_CRITICAL_DEMO_TREATMENT_$ScenarioName`_$i"
            toothNumbers      = "11,12,13,14,15,16,17,18,21,22,23,24,25,26,27,28,31,32,33,34,35,36,37,38,41,42,43,44,45,46,47,48"
            surfaces          = "M,O,D,B,L"
            totalAmount       = 99999
            selectedDateTime  = $selectedDateTime
            clinicId          = $ClinicId
            request_time      = (Get-Date).ToUniversalTime().ToString("o")
        }

        try {
            $result = Invoke-JsonPost -Url "$ApiBase/predict" -Body $payload
            $success++

            Write-Host ("[{0}/{1}] OK  predicted={2} minutes, request_id={3}" -f `
                $i, $Total, $result.predicted_duration_class, $result.request_id)

            if (-not $SkipActual) {
                $actualPayload = [ordered]@{
                    request_id      = $result.request_id
                    actual_duration = $ActualDuration
                    unit            = "minutes"
                }
                $actualResult = Invoke-JsonPost -Url "$ApiBase/actual" -Body $actualPayload
                Write-Host ("        actual logged={0} minutes, status={1}" -f $ActualDuration, $actualResult.status)
            }
        }
        catch {
            $failed++
            Write-Host ("[{0}/{1}] FAILED: {2}" -f $i, $Total, $_.Exception.Message) -ForegroundColor Red
        }

        Start-Sleep -Milliseconds $DelayMs
    }

    Write-Host ""
    Write-Host "Batch succeeded: $success" -ForegroundColor Green
    Write-Host "Batch failed   : $failed"
}

Write-Step "DentTime Critical Alert Demo"

Write-Host "API Base: $ApiBase"
Write-Host "F1 critical batch size        : $F1CriticalTotal"
Write-Host "F1 critical actual duration   : $F1ActualDuration minutes"
Write-Host "Under-estimation batch size   : $UnderEstCriticalTotal"
Write-Host "Under-estimation actual label : $UnderEstActualDuration minutes"
Write-Host "Send /actual labels           : $(-not $SkipActual)"

Write-Step "Checking API"
$root = Invoke-RestMethod -Uri "$ApiBase/" -Method Get
Write-Host "API status: $($root.message)" -ForegroundColor Green

$beforeMetrics = Get-MetricsText
$beforeCount = Get-MetricValue -MetricsText $beforeMetrics -MetricName "denttime_logged_predictions_total"
$beforeF1 = Get-MetricValue -MetricsText $beforeMetrics -MetricName "denttime_macro_f1"
$beforeBaselineF1 = Get-MetricValue -MetricsText $beforeMetrics -MetricName "denttime_macro_f1_baseline"
$beforeUnder = Get-MetricValue -MetricsText $beforeMetrics -MetricName "denttime_underestimation_rate"
$beforeUnderBaseline = Get-MetricValue -MetricsText $beforeMetrics -MetricName "denttime_underestimation_rate_baseline"
$beforeMissing = Get-MetricValue -MetricsText $beforeMetrics -MetricName "denttime_input_missing_rate"

Write-Host "Before logged predictions : $beforeCount"
Write-Host "Before Macro F1           : $beforeF1"
Write-Host "Before Macro F1 baseline  : $beforeBaselineF1"
Write-Host "Before under-estimation   : $beforeUnder"
Write-Host "Before under baseline     : $beforeUnderBaseline"
Write-Host "Before missing rate       : $beforeMissing"

Send-ScenarioBatch `
    -ScenarioName "MACRO_F1_CRITICAL" `
    -Total $F1CriticalTotal `
    -ActualDuration $F1ActualDuration `
    -MinuteOffsetBase 0 `
    -ClinicId "CLINIC_MACRO_F1_CRITICAL"

Send-ScenarioBatch `
    -ScenarioName "UNDER_EST_CRITICAL" `
    -Total $UnderEstCriticalTotal `
    -ActualDuration $UnderEstActualDuration `
    -MinuteOffsetBase 10000 `
    -ClinicId "CLINIC_UNDER_EST_CRITICAL"

Write-Step "Waiting for metrics_updater and Prometheus scrape"
Write-Host "Waiting 45 seconds because metrics_updater refreshes monitoring/state.json every ~15 seconds and Prometheus scrapes /metrics periodically."
Start-Sleep -Seconds 45

$afterMetrics = Get-MetricsText
$afterCount = Get-MetricValue -MetricsText $afterMetrics -MetricName "denttime_logged_predictions_total"
$afterF1 = Get-MetricValue -MetricsText $afterMetrics -MetricName "denttime_macro_f1"
$afterBaselineF1 = Get-MetricValue -MetricsText $afterMetrics -MetricName "denttime_macro_f1_baseline"
$afterUnder = Get-MetricValue -MetricsText $afterMetrics -MetricName "denttime_underestimation_rate"
$afterUnderBaseline = Get-MetricValue -MetricsText $afterMetrics -MetricName "denttime_underestimation_rate_baseline"
$afterMissing = Get-MetricValue -MetricsText $afterMetrics -MetricName "denttime_input_missing_rate"
$afterMae = Get-MetricValue -MetricsText $afterMetrics -MetricName "denttime_mae_minutes"

Write-Step "Summary"
Write-Host "Before logged predictions : $beforeCount"
Write-Host "After logged predictions  : $afterCount"
if ($beforeCount -ne $null -and $afterCount -ne $null) {
    Write-Host "Added rows                : $($afterCount - $beforeCount)" -ForegroundColor Green
}
Write-Host "After Macro F1            : $afterF1"
Write-Host "After Macro F1 baseline   : $afterBaselineF1"
Write-Host "After MAE minutes         : $afterMae"
Write-Host "After under-estimation    : $afterUnder"
Write-Host "After under baseline      : $afterUnderBaseline"
Write-Host "After missing rate        : $afterMissing"

if (
    $afterF1 -ne $null -and
    $afterBaselineF1 -ne $null -and
    $afterUnder -ne $null -and
    $afterUnderBaseline -ne $null -and
    $afterMissing -ne $null
) {
    Get-AlertExpectation `
        -MacroF1 $afterF1 `
        -MacroF1Baseline $afterBaselineF1 `
        -UnderRate $afterUnder `
        -UnderRateBaseline $afterUnderBaseline `
        -MissingRate $afterMissing
}

Write-Step "Open these pages for the teacher demo"
Write-Host "Grafana dashboard : http://localhost:3000/d/denttime-prometheus/denttime-monitoring-dashboard?orgId=1"
Write-Host "Prometheus alerts : http://localhost:9090/alerts"
Write-Host "Backend metrics   : http://localhost:8000/metrics"
Write-Host ""
Write-Host "If Prometheus still shows Pending, wait about 1 minute because the alert rules use 'for: 1m'." -ForegroundColor Yellow
