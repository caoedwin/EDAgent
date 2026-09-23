# EdAgent GPU 检测（Windows / Docker Desktop - WSL2）
# 输出 gpu 或 cpu
$ErrorActionPreference = "Stop"

$nvidiaSmi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
if (-not $nvidiaSmi) { $nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue }

if ($nvidiaSmi) {
    try {
        & nvidia-smi *> $null
        if ($LASTEXITCODE -eq 0) {
            & docker run --rm --gpus all nvidia/cuda:12.6.1-base-ubuntu24.04 nvidia-smi *> $null
            if ($LASTEXITCODE -eq 0) {
                Write-Output "gpu"
                exit 0
            }
        }
    } catch {
        # 落入 CPU 回退
    }
}

Write-Output "cpu"
exit 0
