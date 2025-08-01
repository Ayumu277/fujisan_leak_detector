# Gunicorn configuration for Render deployment
import os

# Renderは環境変数PORTでポートを指定
port = os.environ.get("PORT", "8000")
bind = f"0.0.0.0:{port}"

# より安全な設定でスタート
workers = 1  # 最初は1ワーカーで
timeout = 180  # タイムアウトを延長
keepalive = 2
max_requests = 500
preload_app = False  # preloadを無効にして問題を回避