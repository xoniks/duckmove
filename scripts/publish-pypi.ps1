<#
.SYNOPSIS
  Build duckmove and upload it to the real PyPI.

.DESCRIPTION
  The target index is hard-coded. Credentials come from .env in the repo root
  (never committed):

      PYPI_TOKEN=pypi-<your pypi.org token>

  PyPI and TestPyPI accounts are separate -- a TestPyPI token is rejected here.

  This is IRREVERSIBLE: a released version can never be re-uploaded or
  replaced, only yanked. The script therefore refuses to publish a dirty or
  untagged working tree unless you override it.

.PARAMETER SkipTests
  Upload without running the test suite first.

.PARAMETER Force
  Skip the confirmation prompt and the clean-working-tree check.

.EXAMPLE
  .\scripts\publish-pypi.ps1
#>
[CmdletBinding()]
Param(
  [switch]$SkipTests,
  [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoUrl     = 'https://upload.pypi.org/legacy/'
$ProjectBase = 'https://pypi.org/project/'
$TokenVars   = @('PYPI_TOKEN', 'TWINE_PASSWORD')

function Info($msg) { Write-Host "[pypi] $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "[pypi] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "[pypi] ERROR: $msg" -ForegroundColor Red; exit 1 }

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
  Fail "No token. Put PYPI_TOKEN=pypi-... in .env (get it from https://pypi.org/manage/account/token/)"
}
if ($token -eq 'pypi-REPLACE_ME') {
  Fail '.env still holds the placeholder token; paste your real PyPI token'
}
$env:TWINE_USERNAME = '__token__'
$env:TWINE_PASSWORD = $token

# --- version ---------------------------------------------------------------
$nameMatch = Get-Content 'pyproject.toml' | Select-String -Pattern '^name\s*=\s*"([^"]+)"'
$verMatch  = Get-Content 'pyproject.toml' | Select-String -Pattern '^version\s*=\s*"([^"]+)"'
if (-not $nameMatch -or -not $verMatch) { Fail 'Could not read name/version from pyproject.toml' }
$pkgName = $nameMatch.Matches.Groups[1].Value
$version = $verMatch.Matches.Groups[1].Value

# pyproject and the package must not disagree about the version.
$initVer = python -c "import sys; sys.path.insert(0, 'src'); import duckmove; print(duckmove.__version__)"
if ($LASTEXITCODE -ne 0) { Fail 'Could not import duckmove to read __version__' }
if ($initVer.Trim() -ne $version) {
  Fail "Version mismatch: pyproject.toml says $version, duckmove.__version__ says $($initVer.Trim())"
}

# --- release hygiene -------------------------------------------------------
if (-not $Force) {
  $dirty = & git status --porcelain
  if ($LASTEXITCODE -eq 0 -and $dirty) {
    Warn 'Working tree has uncommitted changes:'
    $dirty | Select-Object -First 10 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    Fail 'Refusing to publish code that is in no commit. Commit first, or pass -Force.'
  }

  $tag = & git tag --points-at HEAD
  if ($LASTEXITCODE -eq 0 -and -not $tag) {
    Warn "HEAD is not tagged. Consider: git tag v$version; git push --tags"
  }
}

Info "About to publish $pkgName $version to REAL PyPI at $RepoUrl"
Warn 'This cannot be undone. A released version can never be replaced, only yanked.'
if (-not $Force) {
  $answer = Read-Host "Type the version ($version) to confirm"
  if ($answer -ne $version) { Fail 'Confirmation did not match; nothing uploaded.' }
}

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
Info "Uploading $($dists.Count) artifact(s) to PyPI"
python -m twine upload --repository-url $RepoUrl $dists
if ($LASTEXITCODE -ne 0) {
  Fail "Upload failed. A 400 usually means version $version already exists on PyPI -- bump the version and rebuild."
}

Info "Done: $ProjectBase$pkgName/"
Write-Host ''
Info 'Verify from a clean venv:'
Write-Host "  python -m venv `$env:TEMP\frompypi" -ForegroundColor Gray
Write-Host "  `$env:TEMP\frompypi\Scripts\python.exe -m pip install $pkgName==$version" -ForegroundColor Gray
Write-Host "  `$env:TEMP\frompypi\Scripts\duckmove.exe doctor" -ForegroundColor Gray
