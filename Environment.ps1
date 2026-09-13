$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$env:TEMP = Join-Path $PSScriptRoot '.temp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $taskRoot '.task-cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $taskRoot '.task-python'
$env:UV_PYTHON_BIN_DIR = Join-Path $taskRoot '.task-python\bin'
$env:UV_LINK_MODE = 'copy'
$env:HF_HOME = Join-Path $PSScriptRoot '.cache\huggingface'
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME 'hub'
$env:HUGGINGFACE_HUB_CACHE = $env:HF_HUB_CACHE
$env:XDG_CACHE_HOME = Join-Path $PSScriptRoot '.cache'
$env:TORCH_HOME = Join-Path $env:XDG_CACHE_HOME 'torch'
$env:PIP_CACHE_DIR = Join-Path $env:XDG_CACHE_HOME 'pip'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONUTF8 = '1'
foreach ($folder in @($env:TEMP, $env:UV_CACHE_DIR, $env:UV_PYTHON_INSTALL_DIR, $env:HF_HUB_CACHE, $env:TORCH_HOME, $env:PIP_CACHE_DIR)) {
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
}
