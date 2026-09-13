. "$PSScriptRoot\Environment.ps1"
Set-Location -LiteralPath $PSScriptRoot
try {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'Can uv de cai Python cuc bo. Chua cai gi ngoai thu muc du an.'
    }
    & uv --no-config python install 3.12 --no-bin
    if ($LASTEXITCODE -ne 0) { throw 'Python download failed.' }
    $runtime = Get-ChildItem -LiteralPath $env:UV_PYTHON_INSTALL_DIR -Directory -Filter 'cpython-3.12.*-windows-x86_64-none' | Sort-Object Name -Descending | Select-Object -First 1
    if (-not $runtime) { throw 'Local Python runtime not found.' }
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        & uv --no-config venv --python (Join-Path $runtime.FullName 'python.exe') .venv
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    }
    & uv --no-config pip sync --python .venv\Scripts\python.exe requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & .\.venv\Scripts\python.exe models.py
    if ($LASTEXITCODE -ne 0) { throw 'Model setup failed. Check the message above and rerun Setup.cmd.' }
} catch {
    Write-Host $_ -ForegroundColor Red
    exit 1
}
