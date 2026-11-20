# Never execute on an owner's workstation. Only disposable hosted Windows.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or $env:RUNNER_OS -ne 'Windows') {
    throw 'Installed QA requires GitHub-hosted Windows.'
}
$root = Split-Path -Parent $PSScriptRoot
$cases = @(
    @{ Version = '1.1.3'; Folder = 'v113'; Hash = 'c910ffeaa464db80ee0cfb251f804e98867813889fb4415e30fd34b78e40298f' },
    @{ Version = '1.1.4'; Folder = 'v114'; Hash = '31b70a2d2cb425cd9e3d8e76381a2a7d84dc9c44cd4065c1732d972262390aa3' }
)
$failed = $false
foreach ($case in $cases) {
    $output = Join-Path $root "reports/$($case.Folder)/installed"
    New-Item -ItemType Directory -Force $output | Out-Null
    $download = Join-Path $env:RUNNER_TEMP "evoice-comparison-$($case.Folder)"
    New-Item -ItemType Directory -Force $download | Out-Null
    $installer = Join-Path $download 'E-Voice-Setup-x64.exe'
    $installDir = Join-Path $download 'application'
    $report = [ordered]@{
        version = $case.Version; passed = $false; expected_sha256 = $case.Hash
        fault_injection = $false; scope = 'Unmodified published installer, built-in speech and UI/lifecycle checks'
    }
    $firewall = "EVoice comparison $($case.Folder)"
    try {
        $url = "https://github.com/moldset-mmc/e-voice/releases/download/v$($case.Version)/E-Voice-Setup-x64.exe"
        Invoke-WebRequest -Uri $url -OutFile $installer -TimeoutSec 180
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash.ToLowerInvariant()
        $report.actual_sha256 = $actual
        $report.bytes = (Get-Item -LiteralPath $installer).Length
        $report.file_version = (Get-Item -LiteralPath $installer).VersionInfo.FileVersion
        if ($actual -ne $case.Hash) { throw 'Public asset differs from pinned GitHub digest; will not execute it.' }
        if ($report.file_version -ne "$($case.Version).0") { throw 'Unexpected installer file version.' }
        $args = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOICONS /DIR=`"$installDir`" /LOG=`"$output/setup.log`""
        $setup = Start-Process -FilePath $installer -ArgumentList $args -PassThru
        if (-not $setup.WaitForExit(120000)) {
            taskkill.exe /PID $setup.Id /T /F | Out-Null
            throw 'Installer exceeded external 120-second deadline.'
        }
        $report.installer_exit_code = $setup.ExitCode
        if ($setup.ExitCode -ne 0) { throw "Installer exited $($setup.ExitCode)." }
        $exe = Join-Path $installDir 'EVoice.exe'
        if (-not (Test-Path -LiteralPath $exe)) { throw 'Installed executable missing.' }
        New-NetFirewallRule -DisplayName $firewall -Direction Outbound -Action Block -Program $exe | Out-Null
        # Use the EXACT SAME external checker for both binaries, from QA base 1.1.3.
        python (Join-Path $root 'installer/check_windows.py') $exe --output $output
        $report.checker_exit_code = $LASTEXITCODE
        if ($LASTEXITCODE -ne 0) { throw 'Installed checker failed; see lifecycle report and logs.' }
        $report.passed = $true
    } catch {
        $report.error = $_.ToString()
        Write-Warning "$($case.Version): $_"
        $failed = $true
    } finally {
        Get-NetFirewallRule -DisplayName $firewall -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
        $report | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $output 'package-result.json')
    }
}
if ($failed) { throw 'One or more installed package checks failed; both versions were attempted.' }
