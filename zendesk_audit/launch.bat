@echo off
start "" http://localhost:5003
cd /d "%~dp0"
python app.py
