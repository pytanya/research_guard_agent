# Запуск коллектора Phoenix с флагами для Windows-хоста (см. README, секция Phoenix).
# Отключает неиспользуемые фичи, которые иначе шумят/роняют старт:
#   - code sandboxes (Monty worker падает с 0xc0000135 - отсутствующая DLL);
#   - docs MCP-сервер и внешние ресурсы (лезут в сеть, SSL/DNS ошибки).
# UI остаётся на http://localhost:6006, трейсы - gRPC :4317.
Set-Location $PSScriptRoot
$env:PHOENIX_ENABLE_MCP_SERVER = "false"
$env:PHOENIX_ALLOWED_SANDBOX_PROVIDERS = "NONE"
$env:PHOENIX_ALLOW_EXTERNAL_RESOURCES = "false"
& ".venv\Scripts\phoenix.exe" serve
