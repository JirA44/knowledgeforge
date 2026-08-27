[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".venv")) {
    py -3.10 -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install -e ".[dev]"
& ".venv\Scripts\python.exe" -m pytest
& ".venv\Scripts\python.exe" -m compileall -q app tests
& ".venv\Scripts\python.exe" -c "from pathlib import Path; import yaml; from app.main import app; s=yaml.safe_load(Path('openapi.yaml').read_text(encoding='utf-8')); assert s == app.openapi(); assert s['info']['version'] == '1.0.7'"
