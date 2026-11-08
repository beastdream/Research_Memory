$Root = Split-Path -Parent $PSScriptRoot
$env:HF_HOME = Join-Path $Root ".cache\huggingface"
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME "hub"
$env:HF_XET_CACHE = Join-Path $env:HF_HOME "xet"
$env:HF_HUB_DOWNLOAD_TIMEOUT = "60"
$env:PIP_CACHE_DIR = Join-Path $Root ".cache\pip"
Write-Host "Hugging Face cache: $env:HF_HOME"
