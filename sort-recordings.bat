@echo off
title Meeting Recording Sorter
powershell -ExecutionPolicy Bypass -File "%~dp0sort-recordings.ps1"
pause
