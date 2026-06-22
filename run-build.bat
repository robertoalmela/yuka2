@echo off
cd /d "C:\Users\rober\Desktop\yuka v2"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "build-dataset.ps1" > "build-stdout.log" 2> "build-stderr.log"
echo BUILD_DONE_EXITCODE=%ERRORLEVEL% >> "build.log"