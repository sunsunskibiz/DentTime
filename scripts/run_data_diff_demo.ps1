param(
    [string]$ApiBase = "http://localhost:8001",
    [int]$Total = 80,
    [int]$DelayMs = 120,
    [switch]$SkipActual,
    [int]$ActualDuration = 105
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

function Get-LabeledMetricValue {
    param(
        [Parameter(Mandatory=$true)][string]$MetricsText,
        [Parameter(Mandatory=$true)][string]$MetricName,
        [Parameter(Mandatory=$true)][string]$LabelText
    )

    $line = ($MetricsText -split "`n" | Where-Object {
        $_ -like "$MetricName{$LabelText}*"
    } | Select-Object -First 1)

    if (-not $line) { return $null }

    return [double](($line -split "\s+")[-1])
}

Write-Step "DentTime Data Diff Batch Request Demo"

Write-Host "API Base: $ApiBase"
Write-Host "Total drift requests: $Total"
Write-Host "Delay between requests: $DelayMs ms"
Write-Host "Send /actual labels: $(-not $SkipActual)"
Write-Host "Actual duration used for demo labels: $ActualDuration minutes"

Write-Step "Checking API"
$root = Invoke-RestMethod -Uri "$ApiBase/" -Method Get
Write-Host "API status: $($root.message)" -ForegroundColor Green

$beforeMetrics = Get-MetricsText
$beforeCount = Get-MetricValue -MetricsText $beforeMetrics -MetricName "denttime_logged_predictions_total"
$beforeMissing = Get-MetricValue -MetricsText $beforeMetrics -MetricName "denttime_input_missing_rate"
$beforeUnder = Get-MetricValue -MetricsText $beforeMetrics -MetricName "denttime_underestimation_rate"

Write-Host "Before logged predictions : $beforeCount"
Write-Host "Before missing rate       : $beforeMissing"
Write-Host "Before under-estimation   : $beforeUnder"

Write-Step "Sending intentionally shifted requests"
Write-Host "This demo intentionally creates Data Diff by using:"
Write-Host "1) unseen treatment names -> UNKNOWN treatment class"
Write-Host "2) very large totalAmount -> amount distribution drift"
Write-Host "3) many tooth numbers / many surfaces -> feature distribution drift"
Write-Host "4) missing doctorId and notes -> input missing rate rises"
Write-Host "5) optional actual_duration=105 -> under-estimation/F1 degradation demo"
Write-Host ""

$success = 0
$failed = 0
$requestIds = New-Object System.Collections.Generic.List[string]

for ($i = 1; $i -le $Total; $i++) {
    # Pick a fixed Sunday night appointment to make day/hour distribution visibly different.
    # Python backend uses datetime.fromisoformat(), so avoid the trailing "Z".
    $selectedDateTime = ([datetime]"2026-04-26T02:30:00").AddMinutes($i).ToString("yyyy-MM-ddTHH:mm:ss")

    # Omit doctorId and notes on purpose. Pydantic accepts them as optional.
    # compute_input_missing_rate watches doctorId and notes, so the missing-rate alert should rise.
    $payload = [ordered]@{
        treatmentSymptoms = "UNSEEN_DATA_DIFF_TREATMENT_$i"
        toothNumbers      = "11,12,13,14,15,16,17,18,21,22,23,24,25,26,27,28,31,32,33,34,35,36,37,38,41,42,43,44,45,46,47,48"
        surfaces          = "M,O,D,B,L"
        totalAmount       = 99999
        selectedDateTime  = $selectedDateTime
        clinicId          = "CLINIC_DATA_DIFF_UNKNOWN"
        request_time      = (Get-Date).ToUniversalTime().ToString("o")
    }

    try {
        $result = Invoke-JsonPost -Url "$ApiBase/predict" -Body $payload
        $success++
        $requestIds.Add([string]$result.request_id) | Out-Null

        Write-Host ("[{0}/{1}] OK  predicted={2} {3}, request_id={4}" -f `
            $i, $Total, $result.predicted_duration_class, $result.unit, $result.request_id)

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

Write-Step "Waiting for metrics_updater and Prometheus scrape"
Write-Host "Waiting 35 seconds because metrics_updater refreshes monitoring/state.json every ~15 seconds and Prometheus scrapes /metrics periodically."
Start-Sleep -Seconds 35

$afterMetrics = Get-MetricsText
$afterCount = Get-MetricValue -MetricsText $afterMetrics -MetricName "denttime_logged_predictions_total"
$afterMissing = Get-MetricValue -MetricsText $afterMetrics -MetricName "denttime_input_missing_rate"
$afterUnder = Get-MetricValue -MetricsText $afterMetrics -MetricName "denttime_underestimation_rate"
$afterF1 = Get-MetricValue -MetricsText $afterMetrics -MetricName "denttime_macro_f1"
$afterMae = Get-MetricValue -MetricsText $afterMetrics -MetricName "denttime_mae_minutes"

Write-Step "Summary"
Write-Host "Requests succeeded        : $success" -ForegroundColor Green
Write-Host "Requests failed           : $failed"
Write-Host "Before logged predictions : $beforeCount"
Write-Host "After logged predictions  : $afterCount"
if ($beforeCount -ne $null -and $afterCount -ne $null) {
    Write-Host "Added rows                : $($afterCount - $beforeCount)" -ForegroundColor Green
}
Write-Host "After missing rate        : $afterMissing"
Write-Host "After under-estimation    : $afterUnder"
Write-Host "After macro F1            : $afterF1"
Write-Host "After MAE minutes         : $afterMae"

Write-Step "Important PSI metrics"
$features = @(
    "treatment_class",
    "total_amount",
    "tooth_count",
    "surface_count",
    "appt_day_of_week",
    "appt_hour_bucket",
    "has_dentist_id",
    "clinic_median_duration",
    "clinic_pct_long"
)

foreach ($feature in $features) {
    $value = Get-LabeledMetricValue `
        -MetricsText $afterMetrics `
        -MetricName "denttime_feature_psi" `
        -LabelText "feature=`"$feature`""

    if ($value -ne $null) {
        $status = if ($value -gt 0.25) { "DRIFT" } else { "OK" }
        Write-Host ("{0,-24} PSI={1,8:N4}  {2}" -f $feature, $value, $status)
    }
    else {
        Write-Host ("{0,-24} PSI=not found yet" -f $feature)
    }
}

Write-Step "Open these pages for the teacher demo"
Write-Host "Grafana dashboard : http://localhost:3000/d/denttime-prometheus/denttime-monitoring-dashboard?orgId=1"
Write-Host "Prometheus alerts : http://localhost:9090/alerts"
Write-Host "Backend metrics   : http://localhost:8001/metrics"

Write-Host ""
Write-Host "Demo explanation:"
Write-Host "Data Diff happens because this script sends live input data whose feature distribution is intentionally different from the reference_features.parquet distribution."
Write-Host "The monitoring job computes PSI, writes monitoring/state.json, /metrics exposes denttime_feature_psi, and Prometheus/Grafana show the alert when PSI > 0.25."
