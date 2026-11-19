# Build time only. Ship unmodified x64 VC runtime files from Microsoft's REDIST tree.
param([Parameter(Mandatory)][string]$AppDirectory)
$ErrorActionPreference = 'Stop'
# The build may have been launched by a different edition of PowerShell.
# Load the signing cmdlet belonging to this executable.
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1')
$roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ } | Select-Object -Unique
$versions = foreach ($base in $roots) {
    Get-ChildItem -Path "$base\Microsoft Visual Studio\2022\*\VC\Redist\MSVC\*" -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -as [version] }
}
$directories = foreach ($version in ($versions | Sort-Object { [version]$_.Name } -Descending)) {
    Get-Item -LiteralPath "$($version.FullName)\x64\Microsoft.VC143.CRT" -ErrorAction SilentlyContinue
}
$runtime = $directories | Where-Object {
    (Test-Path -LiteralPath (Join-Path $_.FullName 'vcruntime140.dll')) -and
    (Test-Path -LiteralPath (Join-Path $_.FullName 'vcruntime140_1.dll'))
} | Select-Object -First 1
if (-not $runtime) { throw 'Install the licensed Visual Studio x64 C++ Redistributable build component.' }
$records = foreach ($name in 'vcruntime140.dll', 'vcruntime140_1.dll') {
    $file = Get-Item -LiteralPath (Join-Path $runtime.FullName $name)
    $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(?:^|,\s*)O=Microsoft Corporation(?:,|$)') {
        throw "Invalid Microsoft signature on $name"
    }
    $destination = Join-Path $AppDirectory "_internal\$name"
    Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    [ordered]@{ name = $name; version = $file.VersionInfo.FileVersion
                source = "VC/Redist/MSVC/$($runtime.Parent.Parent.Name)/x64/$($runtime.Name)/$name"
                sha256 = (Get-FileHash -LiteralPath $destination).Hash.ToLowerInvariant()
                signer = $signature.SignerCertificate.Subject }
}
$records | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $AppDirectory 'licenses\Microsoft-runtime-files.json')
