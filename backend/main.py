from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import json
import uuid
from datetime import datetime
from typing import Dict, List
from io import BytesIO
from dotenv import load_dotenv
from PIL import Image

# 環境変数を読み込み
load_dotenv()

app = FastAPI(title="Book Leak Detector", version="1.0.0")

# 環境変数からAPI_KEYを取得
API_KEY = os.getenv("API_KEY") or os.getenv("SERPAPI_KEY")
if not API_KEY:
    print("警告: API_KEYが設定されていません。.envファイルでAPI_KEYまたはSERPAPI_KEYを設定してください。")

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

@app.get("/")
async def root():
    return {
        "message": "Book Leak Detector API",
        "api_key_configured": API_KEY is not None,
        "upload_count": len(upload_records)
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
        "api_key_configured": API_KEY is not None,
        "upload_directory_exists": os.path.exists(UPLOAD_DIR),
        "records_file_exists": os.path.exists(RECORDS_FILE),
        "total_uploads": len(upload_records)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)