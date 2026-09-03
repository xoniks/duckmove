<#
.SYNOPSIS
  Build duckmove and upload it to TestPyPI.

.DESCRIPTION
  The target index is hard-coded, so this script can never reach real PyPI.
  Credentials come from .env in the repo root (never committed):

      TESTPYPI_TOKEN=pypi-<your test.pypi.org token>

  TestPyPI accounts and tokens are SEPARATE from PyPI ones -- a real PyPI
  token will be rejected here.

.PARAMETER SkipTests
  Upload without running the test suite first.

.EXAMPLE
  .\scripts\publish-testpypi.ps1
#>
[CmdletBinding()]
Param(
  [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoUrl     = 'https://test.pypi.org/legacy/'
$ProjectBase = 'https://test.pypi.org/project/'
$TokenVars   = @('TESTPYPI_TOKEN', 'TWINE_PASSWORD')

function Info($msg) { Write-Host "[testpypi] $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "[testpypi] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "[testpypi] ERROR: $msg" -ForegroundColor Red; exit 1 }

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# --- credentials -----------------------------------------------------------
# Simple KEY=VALUE parser. `$env:$k` is a syntax error in PowerShell, so the
# variable name has to be built as a provider path.
$envPath = Join-Path $root '.env'
if (Test-Path $envPath) {
  Info 'Loading .env'
  foreach ($line in (Get-Content $envPath)) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
    $idx = $trimmed.IndexOf('=')
    if ($idx -le 0) { continue }
    $k = $trimmed.Substring(0, $idx).Trim()
    $v = $trimmed.Substring($idx + 1).Trim().Trim('"').Trim("'")
    Set-Item -Path ('env:' + $k) -Value $v
  }
} else {
  Warn "No .env found at $envPath"
}

$token = $null
foreach ($name in $TokenVars) {
  $candidate = [Environment]::GetEnvironmentVariable($name)
  if ($candidate) { $token = $candidate; break }
}
if (-not $token) {
  Fail "No token. Put TESTPYPI_TOKEN=pypi-... in .env (get it from https://test.pypi.org/manage/account/token/)"
}
if ($token -eq 'pypi-REPLACE_ME') {
  Fail '.env still holds the placeholder token; paste your real TestPyPI token'
}
$env:TWINE_USERNAME = '__token__'
$env:TWINE_PASSWORD = $token

# --- version ---------------------------------------------------------------
$nameMatch = Get-Content 'pyproject.toml' | Select-String -Pattern '^name\s*=\s*"([^"]+)"'
$verMatch  = Get-Content 'pyproject.toml' | Select-String -Pattern '^version\s*=\s*"([^"]+)"'
if (-not $nameMatch -or -not $verMatch) { Fail 'Could not read name/version from pyproject.toml' }
$pkgName = $nameMatch.Matches.Groups[1].Value
$version = $verMatch.Matches.Groups[1].Value
Info "Publishing $pkgName $version"
Warn "TestPyPI accepts each version only ONCE. If $version is already there, bump"
Warn "the version (e.g. $version.dev2) -- re-uploading is not possible."

# --- toolchain -------------------------------------------------------------
# A non-zero exit from a native exe is NOT a terminating error, so try/catch
# does not work here; every step checks $LASTEXITCODE explicitly.
python -c "import build, twine" 2>$null
if ($LASTEXITCODE -ne 0) {
  Info 'Installing build + twine'
  python -m pip install -U build twine | Out-Null
  if ($LASTEXITCODE -ne 0) { Fail 'Could not install build/twine' }
}

if (-not $SkipTests) {
  Info 'Running tests'
  python -m pytest -q
  if ($LASTEXITCODE -ne 0) { Fail 'Tests failed; nothing uploaded (use -SkipTests to override)' }
}

# --- build -----------------------------------------------------------------
Info 'Cleaning dist/ and build/'
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $root 'dist')
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $root 'build')

Info 'Building sdist and wheel'
python -m build
if ($LASTEXITCODE -ne 0) { Fail 'Build failed' }

$dists = @(Get-ChildItem -Path (Join-Path $root 'dist') -File | ForEach-Object { $_.FullName })
if ($dists.Count -eq 0) { Fail 'Build produced no artifacts in dist/' }

Info 'Checking metadata'
python -m twine check $dists
if ($LASTEXITCODE -ne 0) { Fail 'twine check failed' }

# --- upload ----------------------------------------------------------------
Info "Uploading $($dists.Count) artifact(s) to TestPyPI"
python -m twine upload --repository-url $RepoUrl $dists
if ($LASTEXITCODE -ne 0) {
  Fail "Upload failed. A 400 usually means version $version already exists on TestPyPI -- bump the version and rebuild."
}

Info "Done: $ProjectBase$pkgName/"
Write-Host ''
Info 'Install it in a clean venv to verify (deps come from real PyPI):'
Write-Host "  python -m venv `$env:TEMP\fromtest" -ForegroundColor Gray
Write-Host "  `$env:TEMP\fromtest\Scripts\python.exe -m pip install $pkgName==$version --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple" -ForegroundColor Gray
Write-Host "  `$env:DUCKMOVE_DB = `"`$env:TEMP\fromtest_data\data.duckdb`"" -ForegroundColor Gray
Write-Host "  `$env:TEMP\fromtest\Scripts\duckmove.exe doctor" -ForegroundColor Gray
