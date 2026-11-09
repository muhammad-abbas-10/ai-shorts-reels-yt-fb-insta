$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
$frontend = Join-Path $project 'frontend'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python environment is missing. Run: py -3.14 -m venv .venv'
}
if (-not (Test-Path -LiteralPath (Join-Path $frontend 'node_modules'))) {
    throw 'Frontend packages are missing. Run: cd frontend; npm install'
}
& $python -c 'import fastapi, uvicorn, multipart'
if ($LASTEXITCODE -ne 0) {
    throw 'Web API packages are missing. Run: .\.venv\Scripts\python.exe -m pip install -e ".[web]"'
}

Push-Location $frontend
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}
finally {
    Pop-Location
}

Set-Location $project
Write-Host 'AI Shorts Studio: http://127.0.0.1:8000'
& $python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
