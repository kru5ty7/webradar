# Bootstrap for setup_windows.py on a machine that may have no Python yet.
# Run from an administrator PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\setup_windows.ps1 [--build] [--check]

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Find-Python {
    # The py launcher is the reliable entry point; "python" may be the Store stub.
    foreach ($cand in @(@("py", "-3"), @("python"))) {
        $exe = Get-Command $cand[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        $args_ = @($cand | Select-Object -Skip 1) + @("-c", "import sys; print(sys.version_info >= (3, 10))")
        try { $ok = & $exe.Source @args_ 2>$null } catch { continue }
        if ($ok -eq "True") { return ,@($cand) }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host "Python 3.10+ not found - installing Python 3.12 via winget..."
    winget install --id Python.Python.3.12 --exact --silent --accept-package-agreements --accept-source-agreements
    # Pick up the new PATH without reopening the terminal.
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
    $py = Find-Python
    if (-not $py) { throw "Python install finished but no interpreter was found. Open a new admin PowerShell and re-run." }
}

$exe = $py[0]; $pre = @($py | Select-Object -Skip 1)
& $exe @pre "setup_windows.py" @args
exit $LASTEXITCODE
