# Gunicorn configuration for Render deployment (軽量化版)
import os

# Renderは環境変数PORTでポートを指定
port = os.environ.get("PORT", "8000")
bind = f"0.0.0.0:{port}"

# FastAPI (ASGI) 対応のワーカークラス
worker_class = "uvicorn.workers.UvicornWorker"

# 軽量化設定（メモリ不足対策）
workers = 1  # 1ワーカーのみ（メモリ節約）
timeout = 30  # 30秒制限（Renderの制限に合わせる）
keepalive = 2
max_requests = 100  # リクエスト数を制限してメモリリーク防止
max_requests_jitter = 10
preload_app = False
worker_tmp_dir = "/dev/shm"  # メモリ上の一時ディレクトリを使用

# メモリ制限
worker_memory_limit = 512 * 1024 * 1024  # 512MB制限