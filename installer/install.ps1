# Install the public Windows release for the current user. No elevation or Python required.
param([string]$FromDirectory, [string]$LogPath)
$ErrorActionPreference = 'Stop'
$release = 'https://github.com/moldset-mmc/e-voice/releases/download/v1.1.3'
$name = 'E-Voice-Setup-x64.exe'
if (-not [Environment]::Is64BitOperatingSystem) { throw 'E Voice requires 64-bit Windows.' }
$directory = Join-Path ([IO.Path]::GetTempPath()) ('e-voice-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $directory
$file = Join-Path $directory $name
try {
    $sums = if ($FromDirectory) { Get-Content -Raw -LiteralPath (Join-Path $FromDirectory 'SHA256SUMS.txt') } else { Invoke-RestMethod "$release/SHA256SUMS.txt" }
    $expected = $sums -split '\r?\n' |
        Where-Object { $_ -cmatch ('^[0-9a-f]{64}  ' + [regex]::Escape($name) + '$') }
    if (@($expected).Count -ne 1) { throw 'Missing release checksum.' }
    if ($FromDirectory) {
        Copy-Item -LiteralPath (Join-Path $FromDirectory $name) -Destination $file
    } else {
        Invoke-WebRequest -UseBasicParsing -Uri "$release/$name" -OutFile $file
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash -ne $expected.Substring(0, 64)) {
        throw 'The downloaded installer does not match the published checksum.'
    }
    $arguments = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART'
    if ($LogPath) {
        $LogPath = [IO.Path]::GetFullPath($LogPath)
        $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath)
        $arguments += ' /LOG="' + $LogPath + '"'
    }
    $result = Start-Process -FilePath $file -ArgumentList $arguments -Wait -PassThru
    if ($result.ExitCode -ne 0) { throw "Setup exited with code $($result.ExitCode). Close E Voice before updating." }
    Write-Output 'E Voice is installed. Open it from the Start menu.'
} finally {
    if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file }
    [IO.Directory]::Delete($directory)
}
