from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import json
import uuid
import base64
from datetime import datetime
from typing import Dict, List, Optional
from io import BytesIO
from dotenv import load_dotenv
from PIL import Image
import serpapi

# 環境変数を読み込み
load_dotenv()

app = FastAPI(title="Book Leak Detector", version="1.0.0")

# 環境変数から各種API_KEYを取得
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

# API_KEYの設定状況をチェック
missing_keys = []
if not GOOGLE_API_KEY:
    missing_keys.append("GOOGLE_API_KEY")
if not GOOGLE_CSE_ID:
    missing_keys.append("GOOGLE_CSE_ID")
if not GEMINI_API_KEY:
    missing_keys.append("GEMINI_API_KEY")
if not SERPAPI_KEY:
    missing_keys.append("SERPAPI_KEY")

if missing_keys:
    print(f"警告: 以下の環境変数が設定されていません: {', '.join(missing_keys)}")
    print("完全な機能を使用するには、.envファイルで以下を設定してください:")
    print("- GOOGLE_API_KEY: Google API用")
    print("- GOOGLE_CSE_ID: Google Custom Search Engine ID用")
    print("- GEMINI_API_KEY: Gemini AI用")
    print("- SERPAPI_KEY: SerpAPI画像検索用")
else:
    print("✓ すべてのAPI_KEYが正常に設定されています")

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # フロントエンドのURL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# アップロードディレクトリを作成
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 静的ファイル設定（アップロード画像用）
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# メモリ内データストレージ（本番環境ではデータベースを使用）
upload_records: Dict[str, Dict] = {}
search_results: Dict[str, List[Dict]] = {}

# JSONファイルでの永続化
RECORDS_FILE = "upload_records.json"

def load_records():
    """JSONファイルから記録を読み込み"""
    global upload_records
    try:
        if os.path.exists(RECORDS_FILE):
            with open(RECORDS_FILE, 'r', encoding='utf-8') as f:
                upload_records = json.load(f)
    except Exception as e:
        print(f"記録の読み込みに失敗: {e}")
        upload_records = {}

def save_records():
    """JSONファイルに記録を保存"""
    try:
        with open(RECORDS_FILE, 'w', encoding='utf-8') as f:
            json.dump(upload_records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"記録の保存に失敗: {e}")

# アプリ起動時に記録を読み込み
load_records()

def validate_image_file(file: UploadFile) -> bool:
    """アップロードされたファイルが有効な画像かどうかを検証"""
    allowed_types = ["image/jpeg", "image/png", "image/jpg", "image/gif", "image/webp"]
    return file.content_type in allowed_types

def encode_image_to_base64(image_path: str) -> str:
    """画像ファイルをBase64エンコードする"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def analyze_domain(url: str) -> tuple[str, bool, str]:
    """URLからドメインを抽出し、公式サイトか判定し、脅威レベルを評価"""
    from urllib.parse import urlparse

    domain = urlparse(url).netloc.lower()

    # 公式サイトかどうかの判定（簡易版）
    official_domains = [
        'amazon.com', 'amazon.co.jp', 'rakuten.co.jp', 'bookwalker.jp',
        'kadokawa.co.jp', 'shogakukan.co.jp', 'kodansha.co.jp',
        'shueisha.co.jp', 'akitashoten.co.jp', 'viz.com'
    ]

    is_official = any(official in domain for official in official_domains)

    # 脅威レベルの評価（簡易版）
    if is_official:
        threat_level = "safe"
    elif any(suspicious in domain for suspicious in ['free', 'download', 'torrent', 'manga']):
        threat_level = "high"
    elif domain.endswith('.com') or domain.endswith('.jp'):
        threat_level = "medium"
    else:
        threat_level = "unknown"

    return domain, is_official, threat_level

async def search_similar_images(image_path: str) -> List[Dict]:
    """SerpAPIを使用して類似画像を検索する"""
    if not SERPAPI_KEY:
        raise HTTPException(status_code=500, detail="SERPAPI_KEYが設定されていません")

    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="指定された画像ファイルが見つかりません")

    try:
        # 画像をBase64エンコード
        encoded_image = encode_image_to_base64(image_path)

        # SerpAPIで画像検索を実行
        client = serpapi.Client(api_key=SERPAPI_KEY)
        results = client.search({
            "engine": "google_reverse_image",
            "image_url": f"data:image/jpeg;base64,{encoded_image}",
            "hl": "ja",
            "gl": "jp"
        })

        # 検索結果を解析
        processed_results = []

        # 類似画像の結果を処理
        if "image_results" in results:
            for item in results["image_results"][:10]:  # 上位10件
                url = item.get("link", "")
                title = item.get("title", "")
                source = item.get("source", "")

                if url and title:
                    domain, is_official, threat_level = analyze_domain(url)

                    processed_results.append({
                        "url": url,
                        "domain": domain,
                        "title": title,
                        "source": source,
                        "is_official": is_official,
                        "threat_level": threat_level,
                        "thumbnail": item.get("thumbnail", "")
                    })

        # テキスト検索結果も処理
        if "inline_images" in results:
            for item in results["inline_images"][:5]:  # 上位5件
                url = item.get("link", "")
                title = item.get("title", "")
                source = item.get("source", "")

                if url and title:
                    domain, is_official, threat_level = analyze_domain(url)

                    processed_results.append({
                        "url": url,
                        "domain": domain,
                        "title": title,
                        "source": source,
                        "is_official": is_official,
                        "threat_level": threat_level,
                        "thumbnail": item.get("thumbnail", "")
                    })

        return processed_results

    except Exception as e:
        print(f"画像検索エラー: {e}")
        raise HTTPException(status_code=500, detail=f"画像検索中にエラーが発生しました: {str(e)}")

@app.get("/")
async def root():
    return {
        "message": "Book Leak Detector API",
        "api_keys": {
            "google_api_key_configured": GOOGLE_API_KEY is not None,
            "google_cse_id_configured": GOOGLE_CSE_ID is not None,
            "gemini_api_key_configured": GEMINI_API_KEY is not None,
            "serpapi_key_configured": SERPAPI_KEY is not None
        },
        "upload_count": len(upload_records),
        "search_results_count": len(search_results)
    }

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    """画像をアップロードして保存する"""

    # ファイル検証
    if not validate_image_file(file):
        raise HTTPException(
            status_code=400,
            detail="無効なファイル形式です。JPEG、PNG、GIF、WebP形式の画像をアップロードしてください。"
        )

    # ファイルサイズ制限（10MB）
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="ファイルサイズが大きすぎます。10MB以下の画像をアップロードしてください。"
        )

    try:
        # 画像の有効性を確認（バイトデータから）
        image = Image.open(BytesIO(content))
        image.verify()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="破損した画像ファイルです。有効な画像をアップロードしてください。"
        )

    # 一意のファイル名を生成
    file_id = str(uuid.uuid4())
    file_extension = os.path.splitext(file.filename)[1].lower()
    safe_filename = f"{file_id}{file_extension}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)

    # ファイルを保存
    try:
        with open(file_path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"ファイルの保存に失敗しました: {str(e)}"
        )

    # 記録を保存
    upload_record = {
        "id": file_id,
        "original_filename": file.filename,
        "saved_filename": safe_filename,
        "file_path": file_path,
        "content_type": file.content_type,
        "file_size": len(content),
        "upload_time": datetime.now().isoformat(),
        "status": "uploaded"
    }

    upload_records[file_id] = upload_record
    save_records()

    return {
        "success": True,
        "file_id": file_id,
        "original_filename": file.filename,
        "saved_filename": safe_filename,
        "file_size": len(content),
        "upload_time": upload_record["upload_time"],
        "file_url": f"/uploads/{safe_filename}"
    }

@app.get("/uploads/history")
async def get_upload_history():
    """アップロード履歴を取得する"""
    # 日付順でソート（新しいものが最初）
    sorted_records = sorted(
        upload_records.values(),
        key=lambda x: x["upload_time"],
        reverse=True
    )

    return {
        "success": True,
        "count": len(sorted_records),
        "uploads": sorted_records
    }

@app.get("/uploads/{file_id}")
async def get_upload_details(file_id: str):
    """特定のアップロードファイルの詳細を取得する"""
    if file_id not in upload_records:
        raise HTTPException(
            status_code=404,
            detail="指定されたファイルが見つかりません。"
        )

    record = upload_records[file_id]

    # ファイルが実際に存在するかチェック
    if not os.path.exists(record["file_path"]):
        record["status"] = "file_missing"
        save_records()

    return {
        "success": True,
        "file": record
    }

@app.delete("/uploads/{file_id}")
async def delete_upload(file_id: str):
    """アップロードファイルを削除する"""
    if file_id not in upload_records:
        raise HTTPException(
            status_code=404,
            detail="指定されたファイルが見つかりません。"
        )

    record = upload_records[file_id]

    # ファイルを削除
    try:
        if os.path.exists(record["file_path"]):
            os.remove(record["file_path"])
    except Exception as e:
        print(f"ファイル削除エラー: {e}")

    # 記録から削除
    del upload_records[file_id]
    save_records()

    return {
        "success": True,
        "message": f"ファイル {record['original_filename']} を削除しました。"
    }

@app.get("/health")
async def health_check():
    """ヘルスチェックエンドポイント"""
    return {
        "status": "healthy",
        "api_keys": {
            "google_api_key_configured": GOOGLE_API_KEY is not None,
            "google_cse_id_configured": GOOGLE_CSE_ID is not None,
            "gemini_api_key_configured": GEMINI_API_KEY is not None,
            "serpapi_key_configured": SERPAPI_KEY is not None
        },
        "system": {
            "upload_directory_exists": os.path.exists(UPLOAD_DIR),
            "records_file_exists": os.path.exists(RECORDS_FILE),
            "total_uploads": len(upload_records),
            "total_search_results": len(search_results)
        }
    }

@app.post("/search/{image_id}")
async def search_image(image_id: str):
    """指定されたimage_idの画像に対して類似画像検索を実行する"""
    # アップロード記録を確認
    if image_id not in upload_records:
        raise HTTPException(
            status_code=404,
            detail="指定されたimage_idが見つかりません。"
        )

    record = upload_records[image_id]
    image_path = record["file_path"]

    try:
        # 類似画像検索を実行
        results = await search_similar_images(image_path)

        # 検索結果をメモリに保存
        search_results[image_id] = results

        # アップロード記録を更新
        record["search_status"] = "completed"
        record["search_time"] = datetime.now().isoformat()
        record["results_count"] = len(results)
        save_records()

        return {
            "success": True,
            "image_id": image_id,
            "results_count": len(results),
            "search_time": record["search_time"],
            "message": f"{len(results)}件の類似画像が見つかりました。"
        }

    except Exception as e:
        # エラー状態を記録
        record["search_status"] = "failed"
        record["search_error"] = str(e)
        record["search_time"] = datetime.now().isoformat()
        save_records()

        raise e

@app.get("/results")
async def get_all_results():
    """すべての検索結果を取得する"""
    return {
        "success": True,
        "total_searches": len(search_results),
        "results": search_results
    }

@app.get("/results/{image_id}")
async def get_search_results(image_id: str):
    """特定のimage_idの検索結果を取得する"""
    if image_id not in search_results:
        raise HTTPException(
            status_code=404,
            detail="指定されたimage_idの検索結果が見つかりません。先に検索を実行してください。"
        )

    results = search_results[image_id]

    # 結果を脅威レベル別に分類
    safe_results = [r for r in results if r["is_official"] or r["threat_level"] == "safe"]
    medium_results = [r for r in results if r["threat_level"] == "medium"]
    high_risk_results = [r for r in results if r["threat_level"] == "high"]

    return {
        "success": True,
        "image_id": image_id,
        "total_results": len(results),
        "analysis": {
            "safe_sources": len(safe_results),
            "medium_risk": len(medium_results),
            "high_risk": len(high_risk_results),
            "official_sources": len([r for r in results if r["is_official"]])
        },
        "results": {
            "all": results,
            "safe": safe_results,
            "medium_risk": medium_results,
            "high_risk": high_risk_results
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)