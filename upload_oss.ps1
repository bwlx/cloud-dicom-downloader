param(
	[Parameter(Mandatory = $true)]
	[string]$Version,

	[Parameter(Mandatory = $true)]
	[string[]]$Files,

	[switch]$UpdateLatest
)

$ErrorActionPreference = "Stop"

function Require-Env([string]$Name) {
	$Value = [Environment]::GetEnvironmentVariable($Name)
	if ([string]::IsNullOrWhiteSpace($Value)) {
		throw "Missing required environment variable: $Name"
	}
	return $Value
}

function Normalize-Prefix([string]$Prefix) {
	if ([string]::IsNullOrWhiteSpace($Prefix)) {
		return ""
	}
	return $Prefix.Trim().Trim("/")
}

function Join-ObjectKey([string]$Prefix, [string]$Suffix) {
	if ([string]::IsNullOrWhiteSpace($Prefix)) {
		return $Suffix.TrimStart("/")
	}
	return "{0}/{1}" -f $Prefix.TrimEnd("/"), $Suffix.TrimStart("/")
}

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ToolsDir = Join-Path $RootDir "build\ossutil"
$OssutilVersion = "2.2.1"
$OssutilZip = Join-Path $ToolsDir "ossutil-$OssutilVersion-windows-amd64.zip"
$OssutilDir = Join-Path $ToolsDir "ossutil-$OssutilVersion-windows-amd64"

$Bucket = Require-Env "ALIYUN_OSS_BUCKET"
$Endpoint = Require-Env "ALIYUN_OSS_ENDPOINT"
$AccessKeyId = Require-Env "ALIYUN_ACCESS_KEY_ID"
$AccessKeySecret = Require-Env "ALIYUN_ACCESS_KEY_SECRET"
$StsToken = [Environment]::GetEnvironmentVariable("ALIYUN_STS_TOKEN")
$Prefix = Normalize-Prefix([Environment]::GetEnvironmentVariable("ALIYUN_OSS_PREFIX"))
$PublicBaseUrl = [Environment]::GetEnvironmentVariable("ALIYUN_OSS_PUBLIC_BASE_URL")

if ($Endpoint -notmatch '^https?://') {
	$Endpoint = "https://$Endpoint"
}

New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null
if (-not (Test-Path $OssutilZip)) {
	$DownloadUrl = "https://gosspublic.alicdn.com/ossutil/v2/$OssutilVersion/ossutil-$OssutilVersion-windows-amd64.zip"
	Invoke-WebRequest -Uri $DownloadUrl -OutFile $OssutilZip
}

if (Test-Path $OssutilDir) {
	Remove-Item -Path $OssutilDir -Recurse -Force
}
Expand-Archive -Path $OssutilZip -DestinationPath $ToolsDir -Force

$OssutilExe = Get-ChildItem -Path $OssutilDir -Filter "ossutil*.exe" -File | Select-Object -First 1 -ExpandProperty FullName
if (-not $OssutilExe) {
	throw "ossutil.exe not found after extraction."
}

$ConfigPath = Join-Path $ToolsDir ".ossutilconfig"
$ConfigArgs = @("config", "-c", $ConfigPath, "-e", $Endpoint, "-i", $AccessKeyId, "-k", $AccessKeySecret)
if (-not [string]::IsNullOrWhiteSpace($StsToken)) {
	$ConfigArgs += @("-t", $StsToken)
}
& $OssutilExe @ConfigArgs | Out-Null

$SummaryLines = New-Object System.Collections.Generic.List[string]
$VersionPrefix = Join-ObjectKey $Prefix $Version
$LatestPrefix = Join-ObjectKey $Prefix "latest"

foreach ($File in $Files) {
	if (-not (Test-Path $File)) {
		throw "File not found: $File"
	}

	$Name = Split-Path -Leaf $File
	$VersionKey = Join-ObjectKey $VersionPrefix $Name
	$VersionTarget = "oss://$Bucket/$VersionKey"
	& $OssutilExe cp $File $VersionTarget -c $ConfigPath -f | Out-Null
	Write-Host "Uploaded $Name to $VersionTarget"

	if (-not [string]::IsNullOrWhiteSpace($PublicBaseUrl)) {
		$Base = $PublicBaseUrl.TrimEnd("/")
		$SummaryLines.Add("- $Name: $Base/$VersionKey")
	} else {
		$SummaryLines.Add("- $Name: $VersionTarget")
	}

	if ($UpdateLatest) {
		$LatestKey = Join-ObjectKey $LatestPrefix $Name
		$LatestTarget = "oss://$Bucket/$LatestKey"
		& $OssutilExe cp $File $LatestTarget -c $ConfigPath -f | Out-Null
		Write-Host "Uploaded $Name to $LatestTarget"
	}
}

if ($env:GITHUB_STEP_SUMMARY -and $SummaryLines.Count -gt 0) {
	Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value "### OSS Uploads"
	Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value ($SummaryLines -join [Environment]::NewLine)
}
