param(
	[string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

$AppName = "Cloud DICOM Downloader"
$DistDir = Join-Path $RootDir "dist"
$AppDir = Join-Path $DistDir $AppName
$SafeVersion = $Version -replace '[^0-9A-Za-z.\-_]+', '-'
$ZipName = "Cloud-DICOM-Downloader-windows-$SafeVersion.zip"
$SetupName = "Cloud-DICOM-Downloader-Setup-$SafeVersion.exe"
$BuildSupportDir = Join-Path $RootDir "build\windows-support"
$VCRedistPath = Join-Path $BuildSupportDir "vc_redist.x64.exe"

New-Item -ItemType Directory -Path $BuildSupportDir -Force | Out-Null
Invoke-WebRequest `
	-Uri "https://aka.ms/vs/17/release/vc_redist.x64.exe" `
	-OutFile $VCRedistPath

python -m pip install -r requirements-packaging.txt
python -m playwright install chromium
python -m PyInstaller --noconfirm cloud_dicom_downloader.spec

$ZipPath = Join-Path $DistDir $ZipName
if (Test-Path $ZipPath) {
	Remove-Item $ZipPath -Force
}
Compress-Archive -Path (Join-Path $AppDir "*") -DestinationPath $ZipPath

$InnoSetup = Get-Command ISCC -ErrorAction SilentlyContinue
if ($InnoSetup) {
	& $InnoSetup.Source `
		"/DMyAppSourceDir=$AppDir" `
		"/DMyOutputDir=$DistDir" `
		"/DMyAppVersion=$SafeVersion" `
		"/DMyOutputBaseFilename=Cloud-DICOM-Downloader-Setup-$SafeVersion" `
		"/DVCRedistPath=$VCRedistPath" `
		cloud_dicom_downloader.iss
	Write-Host "Built $SetupName"
} else {
	Write-Host "ISCC not found; built zip package only."
}

Write-Host "Built $AppDir"
Write-Host "Built $ZipPath"
