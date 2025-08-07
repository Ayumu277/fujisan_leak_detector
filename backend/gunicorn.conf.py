# Gunicorn configuration for Render deployment (軽量化版)
import os

# Renderは環境変数PORTでポートを指定
port = os.environ.get("PORT", "8000")
bind = f"0.0.0.0:{port}"

# FastAPI (ASGI) 対応のワーカークラス
worker_class = "uvicorn.workers.UvicornWorker"

# Gemini AI対応設定（長時間処理対応）
workers = 1  # 1ワーカーのみ（メモリ節約）
timeout = 1800  # 30分タイムアウト（Gemini AI処理対応）
keepalive = 2
max_requests = 50  # リクエスト数をさらに制限してメモリリーク防止
max_requests_jitter = 5
preload_app = False
worker_tmp_dir = "/dev/shm"  # メモリ上の一時ディレクトリを使用

# メモリ制限緩和（Gemini AI処理対応）
worker_memory_limit = 1024 * 1024 * 1024  # 1GB制限に拡張