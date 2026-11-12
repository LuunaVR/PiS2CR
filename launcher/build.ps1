$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m pip install --quiet pyinstaller paramiko
# The Pi side (tools and deploy) is bundled into the exe so setup can copy it to the Pi.
python -m PyInstaller --noconfirm --onefile --windowed --name PiS2CR --distpath dist --workpath "$env:TEMP\pis2cr-build" --specpath "$env:TEMP\pis2cr-build" `
    --hidden-import pis2cr_setup `
    --paths $PSScriptRoot `
    --add-data "$PSScriptRoot\setup.html;." `
    --add-data "$PSScriptRoot\..\tools;bundle\tools" `
    --add-data "$PSScriptRoot\..\deploy;bundle\deploy" `
    pis2cr_launcher.py
Write-Output "Built: $PSScriptRoot\dist\PiS2CR.exe"
