param(
    [string]$ProxyUrl = '',
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$localData = [Environment]::GetFolderPath('LocalApplicationData')
if ([string]::IsNullOrWhiteSpace($localData)) {
    throw '无法定位当前用户的 LocalApplicationData 目录。'
}
$tokenDir = Join-Path $localData 'CodexPulse'
$tokenPath = Join-Path $tokenDir 'bridge-token.txt'

if (-not (Test-Path -LiteralPath $tokenPath)) {
    New-Item -ItemType Directory -Path $tokenDir -Force | Out-Null
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    $newToken = [System.BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
    Set-Content -LiteralPath $tokenPath -Value $newToken -Encoding Ascii -NoNewline
}

$token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
if ($token.Length -lt 32) {
    throw '本地桥接令牌过短，请检查 %LOCALAPPDATA%\CodexPulse\bridge-token.txt'
}

$env:CODEX_WATCH_TOKEN = $token
if ([string]::IsNullOrWhiteSpace($ProxyUrl)) {
    $ProxyUrl = $env:CODEX_WATCH_PROXY
}
if ([string]::IsNullOrWhiteSpace($ProxyUrl)) {
    $ProxyUrl = 'http://127.0.0.1:7897'
}
$env:HTTP_PROXY = $ProxyUrl
$env:HTTPS_PROXY = $ProxyUrl

$baseUrl = "http://127.0.0.1:$Port"
$statusHeaders = @{ 'X-Codex-Watch-Token' = $token }

function Write-BridgeStatus($Status) {
    if ($Status.quotaAvailable) {
        Write-Host "5 小时剩余：$($Status.quota.fiveHour.remainingPercent)%"
        Write-Host "7 天剩余：$($Status.quota.weekly.remainingPercent)%"
    } else {
        Write-Host '额度暂不可用；任务状态仍可同步。'
    }
    Write-Host "正在运行的任务：$($Status.activity.count) 个"
}

$bridgeRunning = $false
try {
    $existingStatus = Invoke-RestMethod -Uri "$baseUrl/api/status" `
        -Headers $statusHeaders `
        -TimeoutSec 3
    if ($existingStatus.ok) {
        Write-Host '桥接已经在运行，无需重复启动。'
        Write-BridgeStatus $existingStatus
        return
    }
} catch {
    try {
        $bridgeRunning = (Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 2).ok
    } catch {
        # Nothing is listening yet; start the local bridge below.
    }
}
if ($bridgeRunning) {
    throw "$Port 端口已有服务在运行，但当前令牌无法访问。请先关闭旧桥接再重试。"
}

$python = (Get-Command python -ErrorAction Stop).Source
$bridgePath = Join-Path $projectRoot 'bridge\codex_watch_bridge.py'
$process = Start-Process -FilePath $python `
    -ArgumentList ('"{0}" --port {1}' -f $bridgePath, $Port) `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 2
$status = Invoke-RestMethod -Uri "$baseUrl/api/status" `
    -Headers $statusHeaders `
    -TimeoutSec 20

Write-Host "桥接已启动（进程 $($process.Id)）。"
Write-BridgeStatus $status
Write-Host '令牌已保存在本机用户目录，不要发送到聊天或提交到 GitHub。'
