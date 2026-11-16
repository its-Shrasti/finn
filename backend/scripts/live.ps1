# FINN live runs on Windows. Run from the repo root, one step at a time:
#
#   powershell -ExecutionPolicy Bypass -File backend\scripts\live.ps1 setup
#   powershell -ExecutionPolicy Bypass -File backend\scripts\live.ps1 probe
#   powershell -ExecutionPolicy Bypass -File backend\scripts\live.ps1 step1      # live snapshot
#   powershell -ExecutionPolicy Bypass -File backend\scripts\live.ps1 research   # NAVINFLUOR + PIIND
#   powershell -ExecutionPolicy Bypass -File backend\scripts\live.ps1 why        # network why-lines via the model
#   powershell -ExecutionPolicy Bypass -File backend\scripts\live.ps1 build      # tests + rebuild dist only
#
# Every step writes its full output to backend\data\runs\. The Gemini key is asked
# for with hidden input and lives only in this process's memory: it is never written
# to a file, never typed on a command line (so it never lands in PowerShell history),
# and never sent anywhere except Google's API, in a request header.

param([Parameter(Position = 0)][string]$Step = "help")
$ErrorActionPreference = "Stop"
$Backend = Split-Path $PSScriptRoot -Parent
$Runs = Join-Path $Backend "data\runs"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"
$env:PYTHONUTF8 = "1"          # Windows defaults to cp1252; FINN data has the rupee sign and non-ASCII names
New-Item -ItemType Directory -Force -Path $Runs | Out-Null
Set-Location $Backend

function Ensure-Key {
    if ($env:GEMINI_API_KEY) { Write-Host "GEMINI_API_KEY is set (value not shown)."; return }
    $sec = Read-Host "Paste your Gemini API key (hidden; kept in memory for this run only)" -AsSecureString
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { $env:GEMINI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
    if (-not $env:GEMINI_API_KEY) { throw "No key entered." }
}

function Need-Venv { if (-not (Test-Path $Py)) { throw "Run 'setup' first (no backend\.venv yet)." } }

function Finn([string[]]$CmdArgs) {
    Write-Host "`n> python -m finn $($CmdArgs -join ' ')" -ForegroundColor Cyan
    & $Py -m finn @CmdArgs
    if ($LASTEXITCODE -ne 0) { throw "finn $($CmdArgs -join ' ') failed (exit $LASTEXITCODE). See $Runs" }
}

function Tests {
    Write-Host "`n> pytest" -ForegroundColor Cyan
    & $Py -m pytest -q -p no:cacheprovider 2>&1 | Out-File -Encoding utf8 (Join-Path $Runs "tests.txt")
    $code = $LASTEXITCODE
    Get-Content (Join-Path $Runs "tests.txt") -Tail 3
    if ($code -ne 0) { throw "Tests failed. See $Runs\tests.txt" }
}

function Build { Finn @("network", "--log", "network"); Tests; Finn @("build-app", "--log", "build") }

switch ($Step) {
    "setup" {
        $base = $null
        foreach ($cand in @(@("py", "-3"), @("python"))) {
            try {
                $ok = & $cand[0] $cand[1..9] -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
                if ($ok -eq "True") { $base = $cand; break }
            } catch { }
        }
        if (-not $base) { throw "Need Python 3.10 or newer. Install it from python.org (tick 'Add to PATH'), then re-run setup." }
        Write-Host "Using: $($base -join ' ')  ($(& $base[0] $base[1..9] --version))"
        if (-not (Test-Path $Py)) { & $base[0] $base[1..9] -m venv (Join-Path $Backend ".venv") }
        & $Py -m pip install --upgrade pip --quiet --log (Join-Path $Runs "pip.log")
        & $Py -m pip install -r requirements.txt --quiet --log (Join-Path $Runs "pip.log")
        if ($LASTEXITCODE -ne 0) { throw "pip install failed. See $Runs\pip.log" }
        & $Py -c "import sys, yfinance, pandas, requests, bs4, pypdf; print('python', sys.version.split()[0], '| yfinance', yfinance.__version__, '| pandas', pandas.__version__)" |
            Out-File -Encoding utf8 (Join-Path $Runs "setup.txt")
        Get-Content (Join-Path $Runs "setup.txt")
        Tests
        Write-Host "`nSetup done." -ForegroundColor Green
    }
    "probe"    { Need-Venv; Ensure-Key; Finn @("probe", "--log", "probe") }
    "step1"    { Need-Venv; Ensure-Key; Finn @("snapshot", "--live", "--log", "snapshot"); Build }
    "research" {
        Need-Venv; Ensure-Key
        Finn @("research", "NAVINFLUOR", "--log", "research_NAVINFLUOR")
        Finn @("research", "PIIND", "--log", "research_PIIND")
        Finn @("snapshot", "--add-research", "--log", "add_research")
        Build
    }
    "why"      { Need-Venv; Ensure-Key; Finn @("network", "--model", "--log", "network_model"); Tests; Finn @("build-app", "--log", "build") }
    "build"    { Need-Venv; Build }
    default    { Get-Content $PSCommandPath -TotalCount 12 }
}
