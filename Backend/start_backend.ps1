param(
    [int]$Port = 8000
)

.\.venv\Scripts\python.exe -m uvicorn codesense.api:app `
    --host 0.0.0.0 `
    --port $Port