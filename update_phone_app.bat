@echo off
rem Double-click to update the phone app (runs update_phone_app.ps1 from this folder).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_phone_app.ps1" %*
