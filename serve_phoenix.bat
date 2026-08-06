@echo off
rem Запуск коллектора Phoenix с флагами для Windows-хоста (см. README, секция Phoenix).
rem Отключает неиспользуемые фичи, которые иначе шумят/роняют старт:
rem   - code sandboxes (Monty worker падает с 0xc0000135 - отсутствующая DLL);
rem   - docs MCP-сервер и внешние ресурсы (лезут в сеть, SSL/DNS ошибки).
rem UI остаётся на http://localhost:6006, трейсы - gRPC :4317.
setlocal
cd /d "%~dp0"
set PHOENIX_ENABLE_MCP_SERVER=false
set PHOENIX_ALLOWED_SANDBOX_PROVIDERS=NONE
set PHOENIX_ALLOW_EXTERNAL_RESOURCES=false
call ".venv\Scripts\phoenix.exe" serve
