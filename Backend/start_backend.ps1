param(
    [int]$Port = 8000,
    [int]$StreamlitPort = 8501,
    [switch]$SkipDocker,
    [switch]$SkipStreamlit
)

$ErrorActionPreference = "Stop"
$BackendRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $BackendRoot

$Python = Join-Path $BackendRoot ".venv\Scripts\python.exe"
$Uvicorn = Join-Path $BackendRoot ".venv\Scripts\uvicorn.exe"
$Streamlit = Join-Path $BackendRoot ".venv\Scripts\streamlit.exe"
$EnvFile = Join-Path $BackendRoot ".env"

if (-not (Test-Path $Python)) {
    throw "Python virtual environment not found at $Python. Create it and install dependencies first."
}
if (-not (Test-Path $EnvFile)) {
    throw "Missing $EnvFile. Copy .env.example to .env and configure GROQ_API_KEY."
}

$env:PYTHONPATH = Join-Path $BackendRoot "src"
$envFileLines = Get-Content $EnvFile
foreach ($line in $envFileLines) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$' -and $line -notmatch '^\s*#') {
        $name = $Matches[1]
        $value = $Matches[2].Trim().Trim('"').Trim("'")
        if ($value) { Set-Item -Path "Env:$name" -Value $value }
    }
}
if (-not $env:GROQ_API_KEY -or $env:GROQ_API_KEY -like "your_*") {
    throw "GROQ_API_KEY is missing or still uses the .env.example placeholder."
}

function Test-ListeningPort([int]$CheckPort) {
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $CheckPort -ErrorAction SilentlyContinue)
}

if (Test-ListeningPort $Port) {
    throw "Port $Port is already in use. Stop the existing API process or choose -Port another port."
}
if (-not $SkipStreamlit -and (Test-ListeningPort $StreamlitPort)) {
    throw "Port $StreamlitPort is already in use. Stop the existing Streamlit process or choose -StreamlitPort another port."
}

if (-not $SkipDocker) {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker is required to start Neo4j. Use -SkipDocker only if Neo4j is already running."
    }
    Write-Host "Starting Neo4j..."
    & docker compose up -d neo4j
    if ($LASTEXITCODE -ne 0) { throw "Neo4j failed to start." }
}

Write-Host "Starting FastAPI on port $Port..."
$apiLog = Join-Path $BackendRoot "api.log"
$apiErrorLog = Join-Path $BackendRoot "api-error.log"
Start-Process -FilePath $Uvicorn -ArgumentList "codesense.api:app --host 0.0.0.0 --port $Port" `
    -WorkingDirectory $BackendRoot -RedirectStandardOutput $apiLog -RedirectStandardError $apiErrorLog

if (-not $SkipStreamlit) {
    Write-Host "Starting Streamlit on port $StreamlitPort..."
    $streamlitLog = Join-Path $BackendRoot "streamlit.log"
    $streamlitErrorLog = Join-Path $BackendRoot "streamlit-error.log"
    Start-Process -FilePath $Streamlit -ArgumentList "run frontend/app.py --server.port $StreamlitPort" `
        -WorkingDirectory $BackendRoot -RedirectStandardOutput $streamlitLog -RedirectStandardError $streamlitErrorLog
}

$healthUrl = "http://localhost:$Port/health"
$ready = $false
$apiResponded = $false
$lastHealth = $null
for ($attempt = 1; $attempt -le 90; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        $apiResponded = $true
        $lastHealth = $health
        if ($health.status -eq "ok") { $ready = $true; break }
    } catch {
        if ($_.Exception.Response) {
            $apiResponded = $true
            try {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $lastHealth = $reader.ReadToEnd() | ConvertFrom-Json
            } catch { }
        }
    }
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    if ($apiResponded) {
        Write-Warning "API is responding but dependencies are degraded. Health details:"
        $lastHealth | ConvertTo-Json -Depth 5
        Write-Warning "CodeSense started with degraded health. Review the failed checks before querying."
    } else {
        Write-Host "API did not respond. See $apiLog"
        if (Test-Path $apiLog) { Get-Content $apiLog -Tail 20 }
        if (Test-Path $apiErrorLog) { Get-Content $apiErrorLog -Tail 20 }
        throw "CodeSense API failed to respond at $healthUrl."
    }
}

Write-Host "CodeSense is ready: API=$healthUrl  Streamlit=http://localhost:$StreamlitPort"