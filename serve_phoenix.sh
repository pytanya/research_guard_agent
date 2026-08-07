#!/usr/bin/env bash
# Запуск коллектора Phoenix с флагами (macOS/Linux, см. README, секция Phoenix).
# Отключает неиспользуемые фичи, которые иначе шумят/роняют старт:
#   - code sandboxes (WASM pre-fetch / Monty worker);
#   - docs MCP-сервер и внешние ресурсы (лезут в сеть).
# UI остаётся на http://localhost:6006, трейсы - gRPC :4317.
set -euo pipefail
cd "$(dirname "$0")"

export PHOENIX_ENABLE_MCP_SERVER=false
export PHOENIX_ALLOWED_SANDBOX_PROVIDERS=NONE
export PHOENIX_ALLOW_EXTERNAL_RESOURCES=false

if [[ -x ".venv/bin/phoenix" ]]; then
  ".venv/bin/phoenix" serve
else
  phoenix serve
fi
