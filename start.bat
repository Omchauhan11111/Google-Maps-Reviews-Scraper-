@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Setup missing. Run: python -m venv .venv
  echo Then run: .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

echo Opening Maps Review Extractor at http://127.0.0.1:5000
start "" http://127.0.0.1:5000
".venv\Scripts\waitress-serve.exe" --host=127.0.0.1 --port=5000 wsgi:app
