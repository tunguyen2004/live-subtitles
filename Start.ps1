. "$PSScriptRoot\Environment.ps1"
$python = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host 'Hay chay Setup.cmd truoc.'
    Read-Host 'Nhan Enter de dong'
    exit 1
}
Start-Process -FilePath $python -ArgumentList ('"' + (Join-Path $PSScriptRoot 'app.py') + '"') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
