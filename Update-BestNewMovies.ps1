<#  Update-BestNewMovies.ps1
    - Runs your movie_finder (or main.py)
    - Ensures TMDB_API_KEY via .env
    - Uses venv Python if available
    - Drops output under /projects/best-new-movies/
    - Commits & pushes to GitHub Pages

    Save as: C:\Users\Bruce\Sites\doomsdae\Update-BestNewMovies.ps1
#>

$ErrorActionPreference = "Stop"

# --- Paths & constants ---
$SiteRoot = "C:\Users\Bruce\Sites\doomsdae"
$ProjectRel = "projects\best-new-movies"
$OutDir   = Join-Path $SiteRoot $ProjectRel
$EnvFile  = Join-Path $SiteRoot ".env"

# --- Choose Python: prefer venv, else py launcher, else system python ---
$PythonCandidates = @(
  (Join-Path $SiteRoot ".venv\Scripts\python.exe"),
  "py.exe",
  "python.exe",
  "python"
)

$Python = $null
foreach ($candidate in $PythonCandidates) {
    try {
        $cmd = Get-Command $candidate -ErrorAction Stop
        $Python = $cmd.Path
        break
    } catch {
        continue
    }
}

if (-not $Python) {
    throw "Python not found. Install Python, or create a venv with:  py -m venv .venv"
}

# --- Find your generator script ---
$candidates = @(
  (Join-Path $SiteRoot "movie_finder.py"),
  (Join-Path $SiteRoot "main.py"),
  (Join-Path $SiteRoot "Best New Movies\movie_finder.py"),
  (Join-Path $SiteRoot "Best New Movies\main.py")
)
$Script = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Script) {
  Write-Host "Could not find movie_finder.py or main.py under $SiteRoot." -ForegroundColor Yellow
  $found = Get-ChildItem $SiteRoot -Recurse -Filter *.py | Select-Object -ExpandProperty FullName
  if ($found) { Write-Host "I do see these .py files:`n$($found -join "`n")" }
  throw "Place your script at one of:`n$($candidates -join "`n")"
}

# --- Ensure output dir exists ---
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# --- Load .env into environment (so child process sees TMDB_API_KEY) ---
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -match '^\s*$') { return }
        if ($_ -match '^([^=]+)=(.*)$') {
            $varName = $matches[1].Trim()
            Set-Item "env:$varName" $matches[2]
        }
    }
}

if (-not $env:TMDB_API_KEY -or [string]::IsNullOrWhiteSpace($env:TMDB_API_KEY)) {
  throw "TMDB_API_KEY is not set. Add it to $EnvFile as: TMDB_API_KEY=YOUR_KEY_HERE"
}

# --- Run the generator (CWD = OutDir). If your script ignores CWD, we post-move files. ---
Push-Location $OutDir
try {
  Write-Host "Using Python: $Python"
  Write-Host "Running: $Script"
  & $Python "$Script"
  if ($LASTEXITCODE -ne 0) { throw "movie_finder.py exited with code $LASTEXITCODE" }
}
finally {
  Pop-Location
}

# --- Post-run: collect outputs even if script wrote them to the repo root ---
$RootHtml = Join-Path $SiteRoot "new_streaming_movies.html"
$RootCsv  = Join-Path $SiteRoot "new_streaming_movies.csv"
$OutHtml  = Join-Path $OutDir   "index.html"
$OutCsv   = Join-Path $OutDir   "new_streaming_movies.csv"

if (Test-Path $RootHtml) { Move-Item $RootHtml $OutHtml -Force }
if (Test-Path $RootCsv)  { Move-Item $RootCsv  $OutCsv  -Force }

# --- Repo hygiene: ensure .gitignore keeps secrets & noise out ---
$GitIgnorePath = Join-Path $SiteRoot ".gitignore"
if (-not (Test-Path $GitIgnorePath)) {
@"
# venv & caches
.venv/
__pycache__/
*.pyc

# local env & editor
.env
.vscode/
"@ | Out-File -Encoding utf8 $GitIgnorePath
}

# --- Initialize repo & remote if needed ---
# First, try to find Git
$GitPaths = @(
    "git.exe",  # Check PATH first
    "${env:ProgramFiles}\Git\bin\git.exe",
    "${env:ProgramFiles(x86)}\Git\bin\git.exe",
    "${env:LocalAppData}\Programs\Git\bin\git.exe"
)

$Git = $null
foreach ($path in $GitPaths) {
    try {
        $cmd = Get-Command $path -ErrorAction Stop
        $Git = $cmd.Path
        break
    } catch {
        continue
    }
}

if (-not $Git) {
    throw "Git not found. Please install Git from https://git-scm.com/downloads"
}

Push-Location $SiteRoot
try {
    if (-not (Test-Path (Join-Path $SiteRoot ".git"))) {
        & $Git init
        & $Git config user.email | Out-Null 2>$null
        & $Git config init.defaultBranch main
        & $Git branch -M main
    }

    $hasOrigin = & $Git remote | Select-String -Quiet "^origin$"
    if (-not $hasOrigin) {
        & $Git remote add origin "https://github.com/doomsdae/doomsdae.github.io.git"
    }

    # Stage & commit
    & $Git add .
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm")
    & $Git commit -m "Update Best New Movies ($stamp)" 2>$null

    # Push (set upstream if first time)
    try {
        & $Git push
    } catch {
        & $Git push -u origin main
    }
}
finally {
  Pop-Location
}

Write-Host "Done. Open: https://doomsdae.github.io/$ProjectRel/"
