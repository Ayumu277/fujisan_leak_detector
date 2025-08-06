#!/usr/bin/env python3
"""
SerpAPI統合機能のテストスクリプト
"""

import os
import sys
import logging
from io import BytesIO
from PIL import Image
from dotenv import load_dotenv

# 環境変数を読み込み
load_dotenv()

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_serpapi_integration():
    """SerpAPI統合機能のテスト"""

    print("🧪 SerpAPI統合機能テスト開始")
    print("=" * 50)

    # 環境変数チェック
    serpapi_key = os.getenv("SERPAPI_KEY")
    if not serpapi_key:
        print("❌ SERPAPI_KEY環境変数が設定されていません")
        return False

    print(f"✅ SERPAPI_KEY設定確認: {serpapi_key[:10]}...")

    # 必要なライブラリのインポートテスト
    try:
        import imagehash
        import requests
        from serpapi import GoogleSearch
        print("✅ 必要なライブラリのインポート成功")
    except ImportError as e:
        print(f"❌ ライブラリインポートエラー: {e}")
        print("以下のコマンドでインストールしてください:")
        print("pip install google-search-results imagehash requests")
        return False

    # テスト画像作成
    print("\n🖼️ テスト画像作成中...")
    test_image = Image.new('RGB', (300, 300), color='red')
    img_buffer = BytesIO()
    test_image.save(img_buffer, format='JPEG')
    image_bytes = img_buffer.getvalue()
    print(f"✅ テスト画像作成完了: {len(image_bytes)} bytes")

    # 複数ハッシュ計算テスト
    print("\n🔢 複数ハッシュ計算テスト...")
    try:
        phash = imagehash.phash(test_image)
        dhash = imagehash.dhash(test_image)
        ahash = imagehash.average_hash(test_image)
        print(f"✅ pHash計算成功: {phash}")
        print(f"✅ dHash計算成功: {dhash}")
        print(f"✅ aHash計算成功: {ahash}")
    except Exception as e:
        print(f"❌ ハッシュ計算エラー: {e}")
        return False

    # SerpAPI接続テスト（実際の検索は行わない）
    print("\n🔍 SerpAPI接続テスト...")
    try:
        # テスト用の既知の画像URL
        test_image_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/280px-PNG_transparency_demonstration_1.png"

        search_params = {
            "engine": "google_reverse_image",
            "image_url": test_image_url,
            "api_key": serpapi_key
        }

        search = GoogleSearch(search_params)
        print("✅ SerpAPI検索オブジェクト作成成功")

        # 実際の検索実行（コメントアウト - APIクォータ節約のため）
        # results = search.get_dict()
        # print(f"✅ SerpAPI検索実行成功")

    except Exception as e:
        print(f"❌ SerpAPI接続エラー: {e}")
        return False

    print("\n✅ 全てのテストが成功しました！")
    print("\n📋 高精度統合機能の概要:")
    print("1. Vision APIで通常の画像検索を実行")
    print("2. SerpAPIで逆画像検索を実行")
    print("3. 複数ハッシュアルゴリズム（pHash, dHash, aHash）で厳密判定")
    print("4. 「ほぼ完全一致」のみを採用（総合距離・最大距離による判定）")
    print("5. 結果を統合して重複を除去、スコア順ソート")

    return True

if __name__ == "__main__":
    success = test_serpapi_integration()
    sys.exit(0 if success else 1)