param(
    [string]$Url = 'https://watch.lmc222.site/api/status'
)

$ErrorActionPreference = 'Stop'

$localData = [Environment]::GetFolderPath('LocalApplicationData')
$tokenPath = Join-Path $localData 'CodexPulse\bridge-token.txt'
if (-not (Test-Path -LiteralPath $tokenPath)) {
    throw '找不到本机桥接令牌；请先运行 scripts/start-bridge.ps1。'
}

$token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
if ($token.Length -lt 32) {
    throw '本机桥接令牌无效。'
}

$parsedUrl = $null
if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$parsedUrl) -or $parsedUrl.Scheme -ne 'https') {
    throw '手表接口地址必须是有效的 HTTPS URL。'
}
$configPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'src\monitor.local.js'
$urlLiteral = ConvertTo-Json -InputObject $Url -Compress
$tokenLiteral = ConvertTo-Json -InputObject $token -Compress
$content = "export const MONITOR_URL = $urlLiteral`nexport const MONITOR_TOKEN = $tokenLiteral`n"
Set-Content -LiteralPath $configPath -Value $content -Encoding UTF8 -NoNewline
Write-Host "已生成本机手表配置，地址为 $Url。"
Write-Host '配置文件已被 .gitignore 排除；请勿上传安装包或分享该文件。'
