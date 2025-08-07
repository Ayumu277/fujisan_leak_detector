# Gunicorn configuration for Render deployment (ASGI対応)
import os

# Renderは環境変数PORTでポートを指定
port = os.environ.get("PORT", "8000")
bind = f"0.0.0.0:{port}"

# FastAPI (ASGI) 対応のワーカークラス
worker_class = "uvicorn.workers.UvicornWorker"

# より安全な設定でスタート
workers = 1  # 最初は1ワーカーで
timeout = 600  # SerpAPI処理用に10分に延長（安全マージン確保）
keepalive = 2
max_requests = 500
preload_app = False  # preloadを無効にして問題を回避