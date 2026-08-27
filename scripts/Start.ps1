[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [string]$DatabasePath = "data/knowledgeforge.db"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".venv")) {
    py -3.10 -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install -e ".[dev]"
$env:KNOWLEDGEFORGE_DB_PATH = $DatabasePath
& ".venv\Scripts\python.exe" -m uvicorn app.main:app --host $HostAddress --port $Port
