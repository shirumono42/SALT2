#!/usr/bin/env bash
# 初回: 仮想環境の作成と依存導入、.env の雛形コピー。その後サーバー起動。
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
.venv/bin/pip install -q -r requirements.txt
if [ ! -f .env ]; then cp .env.example .env; echo ".env を作成しました。ANTHROPIC_API_KEY / ADMIN_PASSWORD / SECRET_KEY を設定して再実行してください。"; exit 1; fi
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
