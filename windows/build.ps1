# Build the Windows installer. Run on Windows in this folder; needs Python 3.12+ and Inno Setup 6.
# windows\navlib\build\{x64,x86}\TDxNavLib.dll must exist (make in windows/navlib, MinGW-w64).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

foreach ($arch in "x64", "x86") {
    if (-not (Test-Path "navlib\build\$arch\TDxNavLib.dll")) { throw "navlib\build\$arch\TDxNavLib.dll missing" }
}
python -m pip install --upgrade --quiet pyside6 bleak pyinstaller
if ($LASTEXITCODE) { throw "pip failed" }
$version = (Select-String -Path ..\tray\phonavigator\__init__.py -Pattern '__version__ = "(.*)"').Matches[0].Groups[1].Value

python make_icon.py
python -m PyInstaller --noconfirm --clean --windowed --name Phonavigator `
    --icon "$PSScriptRoot\build\phonavigator.ico" --paths ..\tray `
    --collect-submodules bleak --collect-submodules winrt `
    --distpath build\dist --workpath build\work --specpath build launcher.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc /Q "/DAppVersion=$version" phonavigator.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Get-Item "build\Phonavigator-$version-setup.exe"
