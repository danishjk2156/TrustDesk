@echo off
rem Switch Console workaround: see claude-shim.js
node "%~dp0claude-shim.js" %*
exit /b %ERRORLEVEL%
