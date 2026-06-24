param(
    [string]$ConfigPath = "C:\Program Files (x86)\AirdPro6.0.3\ConversionConfig.json",
    [int]$MzPrecision = 0,
    [switch]$KeepZeroIntensity,
    [switch]$DropZeroIntensity,
    [switch]$AddColumnCompressionTest,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

function Clone-JsonObject {
    param([Parameter(Mandatory = $true)] $Object)
    return ($Object | ConvertTo-Json -Depth 32 | ConvertFrom-Json)
}

function Set-ConfigProperty {
    param(
        [Parameter(Mandatory = $true)] $Object,
        [Parameter(Mandatory = $true)] [string] $Name,
        [Parameter(Mandatory = $true)] $Value
    )
    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
    } else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Apply-CommonOptions {
    param([Parameter(Mandatory = $true)] $Config)

    if ($MzPrecision -ne 0) {
        Set-ConfigProperty $Config "mzPrecision" $MzPrecision
    }
    if ($KeepZeroIntensity) {
        Set-ConfigProperty $Config "ignoreZeroIntensity" $false
    } elseif ($DropZeroIntensity) {
        Set-ConfigProperty $Config "ignoreZeroIntensity" $true
    }
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "AirdPro config file not found: $ConfigPath"
}

$raw = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
$configMap = $raw | ConvertFrom-Json

if (-not ($configMap.PSObject.Properties.Name -contains "Default")) {
    throw "The config file does not contain a Default config."
}

if ($KeepZeroIntensity -and $DropZeroIntensity) {
    throw "Use only one of -KeepZeroIntensity or -DropZeroIntensity."
}

if (($MzPrecision -ne 0) -and ($MzPrecision -notin @(10000, 100000, 1000000))) {
    throw "MzPrecision should be one of 10000, 100000, or 1000000. Use 0 to inherit Default."
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupPath = "$ConfigPath.bak_$timestamp"
Copy-Item -LiteralPath $ConfigPath -Destination $backupPath

# What is verified locally:
#   AirdEngine.RowCompression = 0
#   AirdEngine.ColumnCompression = 1
#
# What is verified from inspected AirdPro 6.0.3 source:
#   ColumnCompression is not a direct StackLayer/Stack-ZDPD selector.
#
# Aird 2.0's newer adaptive method is represented in AirdPro config by
# autoDecision=true plus the candidate int/byte compressor fields below.
$zdpd = Clone-JsonObject $configMap.Default
Set-ConfigProperty $zdpd "configName" "ZDPD"
Set-ConfigProperty $zdpd "creator" "TrackCodecPublicRelease"
Set-ConfigProperty $zdpd "engine" 0
Set-ConfigProperty $zdpd "autoDecision" $false
Apply-CommonOptions $zdpd

$combo = Clone-JsonObject $configMap.Default
Set-ConfigProperty $combo "configName" "Aird2_ComboComp_Auto"
Set-ConfigProperty $combo "creator" "TrackCodecPublicRelease"
Set-ConfigProperty $combo "engine" 0
Set-ConfigProperty $combo "autoDecision" $true
Apply-CommonOptions $combo

$entries = @(
    @{ Name = "ZDPD"; Value = $zdpd },
    @{ Name = "Aird2_ComboComp_Auto"; Value = $combo }
)

if ($AddColumnCompressionTest) {
    $column = Clone-JsonObject $configMap.Default
    Set-ConfigProperty $column "configName" "ColumnCompression_Test"
    Set-ConfigProperty $column "creator" "TrackCodecPublicRelease"
    Set-ConfigProperty $column "engine" 1
    Set-ConfigProperty $column "autoDecision" $false
    Apply-CommonOptions $column
    $entries += @{ Name = "ColumnCompression_Test"; Value = $column }
}

foreach ($entry in $entries) {
    $name = $entry.Name
    if (($configMap.PSObject.Properties.Name -contains $name) -and -not $Force) {
        Write-Host "Config '$name' already exists; keeping the existing one. Use -Force to replace it."
        continue
    }
    if ($configMap.PSObject.Properties.Name -contains $name) {
        $configMap.$name = $entry.Value
    } else {
        $configMap | Add-Member -NotePropertyName $name -NotePropertyValue $entry.Value
    }
}

$json = $configMap | ConvertTo-Json -Depth 32 -Compress
Set-Content -LiteralPath $ConfigPath -Value $json -Encoding UTF8

Write-Host "Updated: $ConfigPath"
Write-Host "Backup:  $backupPath"
if ($MzPrecision -ne 0) {
    Write-Host "mzPrecision set to: $MzPrecision"
} else {
    Write-Host "mzPrecision inherited from Default: $($configMap.Default.mzPrecision)"
}
if ($KeepZeroIntensity) {
    Write-Host "zero-intensity points: kept (ignoreZeroIntensity=false)"
} elseif ($DropZeroIntensity) {
    Write-Host "zero-intensity points: dropped (ignoreZeroIntensity=true)"
} else {
    Write-Host "zero-intensity handling inherited from Default: ignoreZeroIntensity=$($configMap.Default.ignoreZeroIntensity)"
}
Write-Host "Added/updated configs:"
foreach ($entry in $entries) {
    Write-Host "  $($entry.Name)"
}
Write-Host "Config names now present:"
($configMap.PSObject.Properties.Name | Sort-Object) | ForEach-Object { Write-Host "  $_" }
