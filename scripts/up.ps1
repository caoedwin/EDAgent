# EdAgent 一键启动（Windows / Docker Desktop - WSL2）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\up.ps1
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "已从 .env.example 创建 .env，请修改密码后重新运行" -ForegroundColor Yellow
    exit 1
}

$mode = (& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "detect-gpu.ps1")).Trim()
$composeArgs = @("compose", "-f", "docker-compose.yml")

if ($mode -eq "gpu") {
    Write-Host "检测到 GPU，使用 GPU 模式启动" -ForegroundColor Green
    $composeArgs += @("-f", "docker-compose.gpu.yml")
} else {
    Write-Host "未检测到可用 GPU，使用 CPU 模式启动" -ForegroundColor Green
    $composeArgs += @("-f", "docker-compose.cpu.yml")
}

& docker @composeArgs up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "------------------------------------------------------------" -ForegroundColor Cyan
Write-Host "EdAgent 已启动（首次启动 Ollama 会在后台拉取模型）：" -ForegroundColor Cyan
Write-Host "  API 入口(Nginx): http://localhost:18088"
Write-Host "  API 直连:       http://localhost:18080"
Write-Host "  Jaeger UI:      http://localhost:16686"
Write-Host "  Prometheus:     http://localhost:19090"
Write-Host "  Grafana:        http://localhost:13000"
Write-Host "业务接口请求头：Authorization: Bearer <AGENT_API_KEY>"
