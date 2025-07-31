from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import json
import uuid
# base64 は不要（Vision API WEB_DETECTIONを使用）
import re
import logging
# requests は不要（httpxを使用）
from datetime import datetime
from typing import Dict, List, Optional
from io import BytesIO
from dotenv import load_dotenv
from PIL import Image
# serpapi は不要（Vision API WEB_DETECTIONを使用）
import httpx
from bs4 import BeautifulSoup
from google.cloud import vision
import google.generativeai as genai

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ログ保存用（メモリ内）
system_logs = []
MAX_LOGS = 100  # 最大保存ログ数

class ListHandler(logging.Handler):
    """ログをリストに保存するカスタムハンドラー"""
    def emit(self, record):
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "message": record.getMessage()
        }
        system_logs.append(log_entry)
        # 古いログを削除
        if len(system_logs) > MAX_LOGS:
            system_logs.pop(0)

# カスタムハンドラーを追加
list_handler = ListHandler()
logger.addHandler(list_handler)

# 環境変数を読み込み
load_dotenv()

app = FastAPI(title="Book Leak Detector", version="1.0.0")

# 環境変数から必要なAPI_KEYを取得
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# Gemini APIの設定
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    logger.info("✅ Gemini API設定完了")
else:
    logger.warning("⚠️ GEMINI_API_KEY が設定されていません")

# API_KEYの設定状況をチェック
missing_keys = []
if not GEMINI_API_KEY:
    missing_keys.append("GEMINI_API_KEY")
if not GOOGLE_APPLICATION_CREDENTIALS:
    missing_keys.append("GOOGLE_APPLICATION_CREDENTIALS")

if missing_keys:
    print(f"警告: 以下の環境変数が設定されていません: {', '.join(missing_keys)}")
    print("完全な機能を使用するには、.envファイルで以下を設定してください:")
    print("- GEMINI_API_KEY: Gemini AI用")
    print("- GOOGLE_APPLICATION_CREDENTIALS: Google Vision API用サービスアカウントキー")
else:
    print("✓ 必要なAPI_KEYが正常に設定されています")

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://localhost:5174"],  # フロントエンドのURL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# アップロードディレクトリを作成
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 静的ファイル設定（アップロード画像用）
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# 一時的な画像公開用（検索時のみ使用）
app.mount("/temp-images", StaticFiles(directory=UPLOAD_DIR), name="temp-images")

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

# 公式ドメインリストは削除（Gemini AIで動的判定）

# Vision APIクライアントをグローバルで初期化
vision_client = vision.ImageAnnotatorClient()

# Geminiモデルをグローバルで初期化
if GEMINI_API_KEY:
    gemini_model = genai.GenerativeModel('gemini-1.5-flash')
    logger.info("✅ Gemini モデル初期化完了")
else:
    gemini_model = None
    logger.warning("⚠️ Gemini モデルを初期化できませんでした")

def validate_image_file(file: UploadFile) -> bool:
    """アップロードされたファイルが有効な画像かどうかを検証"""
    allowed_types = ["image/jpeg", "image/png", "image/jpg", "image/gif", "image/webp"]
    return file.content_type in allowed_types

# Base64エンコード関数は削除（不要）

def search_web_for_image(image_content: bytes) -> list[str]:
    """
    画像コンテンツを受け取り、Google Cloud Vision APIのWEB_DETECTIONを使って
    類似・同一画像が使用されているURLのリストを返す。
    """
    logger.info("🔍 Google Vision API WEB_DETECTION検索開始")

    try:
        image = vision.Image(content=image_content)
        response = vision_client.web_detection(image=image)
        web_detection = response.web_detection

        # URLを選別し、ページのURLを優先する
        page_urls = [page.url for page in web_detection.pages_with_matching_images if page.url] if web_detection.pages_with_matching_images else []

        # 画像URLは参考程度に収集
        image_urls = []
        if web_detection.full_matching_images:
            image_urls.extend(img.url for img in web_detection.full_matching_images if img.url)
        if web_detection.partial_matching_images:
            image_urls.extend(img.url for img in web_detection.partial_matching_images if img.url)

        # 重複を除去し、ページURLを優先したリストを作成
        seen = set()
        unique_urls = []

        # page_urls を先に追加
        for url in page_urls:
            if url not in seen:
                unique_urls.append(url)
                seen.add(url)

        # image_urls を追加（既にseenにあるものはスキップ）
        for url in image_urls:
            if url not in seen:
                unique_urls.append(url)
                seen.add(url)

        url_list = unique_urls
        logger.info(f"🌐 発見されたユニークURL: {len(url_list)}件")
        for i, url in enumerate(url_list[:5]):  # 最初の5件をログに表示
            logger.info(f"  {i+1}: {url}")

        return url_list

    except Exception as e:
        logger.error(f"❌ WEB_DETECTION エラー: {str(e)}")
        return []

def scrape_page_content(url: str) -> str | None:
    """
    URLのページからタイトルと本文の一部を抽出する。
    画像URLやHTML以外のコンテンツはスキップする。
    """
    # 1. 拡張子とドメインで簡易フィルタリング
    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
    if any(url.lower().endswith(ext) for ext in image_extensions):
        logger.info(f"⏭️  画像拡張子のためスキップ: {url}")
        return None

    image_host_domains = ['pbs.twimg.com', 'm.media-amazon.com', 'img-cdn.theqoo.net']
    if any(domain in url for domain in image_host_domains):
        logger.info(f"⏭️  画像ホスティングドメインのためスキップ: {url}")
        return None

    logger.info(f"🌐 スクレイピング開始: {url}")
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            # 2. Content-Typeを事前確認
            try:
                head_response = client.head(url, headers={'User-Agent': 'Mozilla/5.0'})
                content_type = head_response.headers.get('content-type', '').lower()
                if 'text/html' not in content_type:
                    logger.info(f"⏭️  HTMLでないためスキップ (Content-Type: {content_type}): {url}")
                    return None
            except httpx.RequestError as e:
                logger.warning(f"⚠️ HEADリクエスト失敗 (GETで続行): {e}")

            # 3. GETリクエストでコンテンツ取得
            response = client.get(url, headers={'User-Agent': 'Mozilla/5.0'})
            response.raise_for_status()

        # 4. BeautifulSoupで解析
        soup = BeautifulSoup(response.text, 'html.parser')
        title = soup.title.string if soup.title else ""
        body_text = " ".join([p.get_text() for p in soup.find_all('p', limit=5)])

        content = f"Title: {title.strip()}\\n\\nBody: {body_text.strip()}"
        logger.info(f"📝 スクレイピング完了: {len(content)} chars")
        return content

    except httpx.HTTPStatusError as e:
        logger.error(f"❌ HTTPステータスエラー {url}: {e.response.status_code} {e.response.reason_phrase}")
        return None
    except Exception as e:
        logger.error(f"❌ スクレイピング一般エラー {url}: {e}")
        return None

def judge_content_with_gemini(content: str) -> dict:
    """スクレイピングした内容をGeminiで判定する"""
    logger.info("🤖 Gemini AI判定開始")

    if not gemini_model:
        logger.error("❌ Gemini モデルが初期化されていません")
        return {"judgment": "？", "reason": "Gemini APIが設定されていません"}

    prompt = f"""
以下のWebページの内容を分析し、著作権的に問題がある海賊版サイトか、
それとも正規のサイトかを判断してください。

【Webページの内容】
{content[:2000]}

【判断基準】
- 正規サイト: 出版社、著者、公式書店、書評、ニュース記事など。
- 海賊版サイト: 全文掲載、無料ダウンロード、違法コピーを示唆する文言など。

        【回答形式】
        以下のフォーマットで回答してください。
        判定：[○、×、？ のいずれか]
        理由：[判断の根拠を20字以内で簡潔に。判断不能な場合は「情報不足のため判断不能」と記載]
"""
    try:
        response = gemini_model.generate_content(prompt)
        logger.info(f"📋 Gemini応答: {response.text[:100]}...")

        # レスポンスから判定と理由を抽出
        lines = response.text.strip().split('\n')
        judgment_line = next((line for line in lines if '判定：' in line), '')
        reason_line = next((line for line in lines if '理由：' in line), '')

        judgment = judgment_line.split('：')[1].replace('[','').replace(']','').strip() if '：' in judgment_line else "？"
        reason = reason_line.split('：')[1].replace('[','').replace(']','').strip() if '：' in reason_line else "AI応答の解析に失敗"

        logger.info(f"✅ Gemini判定完了: {judgment} - {reason}")
        return {"judgment": judgment, "reason": reason}
    except Exception as e:
        error_msg = str(e)

        # エラーの種類別にログと理由を分ける
        if "404" in error_msg and "models/" in error_msg:
            logger.error(f"❌ Gemini API モデルエラー: {error_msg}")
            return {"judgment": "！", "reason": "Geminiモデルが見つかりません"}
        elif "401" in error_msg or "403" in error_msg:
            logger.error(f"❌ Gemini API 認証エラー: {error_msg}")
            return {"judgment": "！", "reason": "Gemini API認証エラー"}
        elif "429" in error_msg or "quota" in error_msg.lower():
            logger.error(f"❌ Gemini API クォータエラー: {error_msg}")
            return {"judgment": "！", "reason": "Gemini APIクォータ制限"}
        elif "timeout" in error_msg.lower() or "connection" in error_msg.lower():
            logger.error(f"❌ Gemini API ネットワークエラー: {error_msg}")
            return {"judgment": "！", "reason": "Geminiネットワークエラー"}
        else:
            logger.error(f"❌ Gemini API 不明エラー: {error_msg}")
            return {"judgment": "？", "reason": f"AI判定エラー: {error_msg[:50]}..."}







# analyze_domain関数は削除（Vision API + Gemini判定を使用）



# 不要な関数は削除されました

# Google Custom Search API関数は削除（Vision API WEB_DETECTIONを使用）

# 画像特徴ベース検索関数は削除（Vision API WEB_DETECTIONを使用）

# SerpAPI関連の関数は削除（Vision API WEB_DETECTIONを使用）

@app.get("/")
async def root():
    return {
        "message": "Book Leak Detector API",
        "api_keys": {
            "gemini_api_key_configured": GEMINI_API_KEY is not None,
            "google_vision_api_configured": GOOGLE_APPLICATION_CREDENTIALS is not None
        },
        "upload_count": len(upload_records),
        "search_results_count": len(search_results)
    }

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    """画像をアップロードして保存する"""

    logger.info(f"📤 アップロード開始: {file.filename}, content_type: {file.content_type}")

    try:
        # ファイル検証
        if not validate_image_file(file):
            logger.error(f"❌ 無効なファイル形式: {file.content_type}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_file_format",
                    "message": "無効なファイル形式です。JPEG、PNG、GIF、WebP形式の画像をアップロードしてください。",
                    "allowed_types": ["image/jpeg", "image/png", "image/jpg", "image/gif", "image/webp"],
                    "received_type": file.content_type
                }
            )

        logger.info("✅ ファイル形式検証OK")

        # ファイルサイズ制限（10MB）
        content = await file.read()
        file_size_mb = len(content) / (1024 * 1024)
        logger.info(f"📊 ファイルサイズ: {file_size_mb:.2f}MB")

        if len(content) > 10 * 1024 * 1024:
            logger.error(f"❌ ファイルサイズが大きすぎます: {file_size_mb:.2f}MB")
            raise HTTPException(
                status_code=413,
                detail={
                    "error": "file_too_large",
                    "message": "ファイルサイズが大きすぎます。10MB以下の画像をアップロードしてください。",
                    "file_size_mb": file_size_mb,
                    "max_size_mb": 10
                }
            )

        try:
            # 画像の有効性を確認（バイトデータから）
            image = Image.open(BytesIO(content))
            image.verify()
            logger.info("✅ 画像有効性検証OK")
        except Exception as e:
            logger.error(f"❌ 画像検証失敗: {str(e)}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "corrupted_image",
                    "message": "破損した画像ファイルです。有効な画像をアップロードしてください。",
                    "validation_error": str(e)
                }
            )

        # 一意のファイル名を生成
        file_id = str(uuid.uuid4())
        file_extension = os.path.splitext(file.filename or "image")[1].lower() or ".jpg"
        safe_filename = f"{file_id}{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, safe_filename)

        logger.info(f"💾 ファイル保存開始: {file_path}")

        # ファイルを保存
        try:
            with open(file_path, "wb") as f:
                f.write(content)
            logger.info("✅ ファイル保存成功")
        except Exception as e:
            logger.error(f"❌ ファイル保存失敗: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "file_save_failed",
                    "message": f"ファイルの保存に失敗しました: {str(e)}",
                    "file_path": file_path
                }
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

        logger.info(f"✅ アップロード完了: file_id={file_id}")

        return {
            "success": True,
            "file_id": file_id,
            "original_filename": file.filename,
            "saved_filename": safe_filename,
            "file_size": len(content),
            "upload_time": upload_record["upload_time"],
            "file_url": f"/uploads/{safe_filename}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 予期しないエラー: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "unexpected_error",
                "message": f"予期しないエラーが発生しました: {str(e)}"
            }
        )

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
            "gemini_api_key_configured": GEMINI_API_KEY is not None,
            "google_vision_api_configured": GOOGLE_APPLICATION_CREDENTIALS is not None
        },
        "system": {
            "upload_directory_exists": os.path.exists(UPLOAD_DIR),
            "records_file_exists": os.path.exists(RECORDS_FILE),
            "total_uploads": len(upload_records),
            "total_search_results": len(search_results)
        }
    }

@app.post("/search/{image_id}")
async def analyze_image(image_id: str):
    """指定された画像IDに対してWeb検索を実行し、類似画像のURLリストを取得する"""

    logger.info(f"🔍 Web画像検索開始: image_id={image_id}")

    # アップロード記録を確認
    if image_id not in upload_records:
        logger.error(f"❌ image_id not found: {image_id}")
        raise HTTPException(
            status_code=404,
            detail={
                "error": "image_not_found",
                "message": "指定されたimage_idが見つかりません。",
                "image_id": image_id
            }
        )

    record = upload_records[image_id]
    image_path = record["file_path"]

    logger.info(f"📁 検索対象画像: {image_path}")

    try:
        # 画像ファイルを開いてコンテンツを読み込む
        with open(image_path, 'rb') as image_file:
            image_content = image_file.read()

        logger.info(f"📸 画像ファイル読み込み完了: {len(image_content)} bytes")

                # Google Vision API WEB_DETECTIONでURL検索
        logger.info("🌐 Google Vision API WEB_DETECTION実行中...")
        url_list = search_web_for_image(image_content)

        logger.info(f"✅ Web検索完了: {len(url_list)}件のURLを発見")

        # 各URLに対してスクレイピング + Gemini判定を実行
        processed_results = []

        for i, url in enumerate(url_list[:10]):  # 最大10件を処理
            logger.info(f"🔄 URL処理中 ({i+1}/{min(len(url_list), 10)}): {url}")

            # ページ内容をスクレイピング
            content = scrape_page_content(url)

            if content:
                # Geminiで判定
                result = judge_content_with_gemini(content)

                processed_results.append({
                    "url": url,
                    "judgment": result['judgment'],
                    "reason": result['reason']
                })

                logger.info(f"  ✅ 処理完了: {result['judgment']} - {result['reason']}")
            else:
                # スクレイピング失敗時
                processed_results.append({
                    "url": url,
                    "judgment": "？",
                    "reason": "ページの内容を取得できませんでした"
                })
                logger.info(f"  ❌ スクレイピング失敗: {url}")

        # 最終結果を保存
        search_results[image_id] = processed_results

        # アップロード記録を更新
        record["analysis_status"] = "completed"
        record["analysis_time"] = datetime.now().isoformat()
        record["found_urls_count"] = len(url_list)
        record["processed_results_count"] = len(processed_results)
        save_records()

        logger.info(f"✅ 分析完了: image_id={image_id}, URL発見={len(url_list)}件, 処理完了={len(processed_results)}件")

        return {
            "success": True,
            "image_id": image_id,
            "found_urls_count": len(url_list),
            "processed_results_count": len(processed_results),
            "results": processed_results,
            "analysis_time": record["analysis_time"],
            "message": f"Web検索・分析が完了しました。{len(url_list)}件のURLが見つかり、{len(processed_results)}件を分析しました。"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Web検索エラー: {str(e)}")

        # エラー状態を記録
        record["analysis_status"] = "failed"
        record["analysis_error"] = str(e)
        record["analysis_time"] = datetime.now().isoformat()
        save_records()

        raise HTTPException(
            status_code=500,
            detail={
                "error": "search_failed",
                "message": f"Web検索中にエラーが発生しました: {str(e)}",
                "image_id": image_id
            }
        )

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
    if image_id not in upload_records:
        raise HTTPException(
            status_code=404,
            detail={"error": "image_not_found", "message": "指定されたimage_idのアップロード記録が見つかりません。"}
        )

    record = upload_records[image_id]

    # 分析がまだ、または失敗している場合
    if record.get("analysis_status") != "completed":
        return {
            "success": True,
            "image_id": image_id,
            "analysis_status": record.get("analysis_status", "not_started"),
            "message": "分析が完了していません。先に分析を実行してください。",
            "details": record.get("analysis_error")
        }

    # 分析は完了したが、有効な結果が0件だった場合
    if record.get("processed_results_count", 0) == 0:
        return {
            "success": True,
            "image_id": image_id,
            "analysis_status": "completed_no_results",
            "message": "分析は完了しましたが、有効なWebページが見つかりませんでした。",
            "found_urls_count": record.get("found_urls_count", 0),
            "processed_results_count": 0,
            "results": []
        }

    # 正常な結果を返す
    return {
        "success": True,
        "image_id": image_id,
        "analysis_status": "completed",
        "original_filename": record.get("original_filename", "不明"),
        "analysis_time": record.get("analysis_time", "不明"),
        "found_urls_count": record.get("found_urls_count", 0),
        "processed_results_count": record.get("processed_results_count", 0),
        "results": search_results.get(image_id, [])
    }

# テスト用エンドポイント
@app.get("/test-search")
async def test_search():
    """ダミー画像で検索テストを実行する"""
    logger.info("🧪 テスト検索開始")

    # ダミーデータを作成
    test_image_id = "test-" + str(uuid.uuid4())

    # テスト用のダミー結果
    dummy_results = [
        {
            "url": "https://amazon.co.jp/test-book",
            "domain": "amazon.co.jp",
            "title": "テスト書籍 - Amazon",
            "source": "Amazon Japan",
            "is_official": True,
            "threat_level": "safe",
            "detailed_analysis": {
                "status": "safe",
                "reason": "公式ドメインです",
                "content_analysis": None
            },
            "thumbnail": "https://example.com/thumb.jpg",
            "analysis_timestamp": datetime.now().isoformat()
        },
        {
            "url": "https://suspicious-site.com/free-download",
            "domain": "suspicious-site.com",
            "title": "無料ダウンロード - 疑わしいサイト",
            "source": "Suspicious Site",
            "is_official": False,
            "threat_level": "suspicious",
            "detailed_analysis": {
                "status": "suspicious",
                "reason": "疑わしいキーワードが検出されました: 無料ダウンロード",
                "content_analysis": "分析対象テキスト（一部）: 無料ダウンロードはこちら..."
            },
            "thumbnail": "https://example.com/thumb2.jpg",
            "analysis_timestamp": datetime.now().isoformat()
        }
    ]

    # テスト結果をメモリに保存
    search_results[test_image_id] = dummy_results

    logger.info(f"✅ テスト検索完了: {len(dummy_results)}件の結果")

    return {
        "success": True,
        "test_image_id": test_image_id,
        "results_count": len(dummy_results),
        "message": f"テスト検索が完了しました。{len(dummy_results)}件の結果があります。",
        "test_results": dummy_results
    }

@app.get("/test-domain/{domain}")
async def test_domain_analysis(domain: str):
    """指定されたドメインの判定テストを実行する（新ワークフロー対応）"""
    logger.info(f"🧪 ドメインテスト開始: {domain}")

    # テスト用URL
    test_url = f"https://{domain}"

    try:
        # ページ内容をスクレイピング
        content = scrape_page_content(test_url)

        if content:
            # Geminiで判定
            result = judge_content_with_gemini(content)

            logger.info(f"✅ ドメインテスト完了: {domain} -> {result['judgment']}")

            return {
                "success": True,
                "domain": domain,
                "test_url": test_url,
                "judgment": result['judgment'],
                "reason": result['reason'],
                "scraped_content_length": len(content),
                "test_time": datetime.now().isoformat()
            }
        else:
            logger.warning(f"⚠️ スクレイピング失敗: {domain}")
            return {
                "success": False,
                "domain": domain,
                "error": "ページの内容を取得できませんでした",
                "test_time": datetime.now().isoformat()
            }

    except Exception as e:
        logger.error(f"❌ ドメインテストエラー: {str(e)}")
        return {
            "success": False,
            "domain": domain,
            "error": str(e),
            "test_time": datetime.now().isoformat()
        }

@app.get("/debug/logs")
async def get_debug_info():
    """デバッグ情報を取得する"""
    return {
        "system_status": "running",
        "total_uploads": len(upload_records),
        "total_search_results": len(search_results),
        "recent_uploads": list(upload_records.keys())[-5:] if upload_records else [],
        "api_keys_status": {
            "gemini_api_key": GEMINI_API_KEY is not None,
            "google_vision_api": GOOGLE_APPLICATION_CREDENTIALS is not None
        },
        "vision_api_status": "active",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/logs")
async def get_system_logs():
    """システムログを取得する"""
    logger.info(f"📋 ログ取得要求: {len(system_logs)}件のログ")
    return {
        "success": True,
        "total_logs": len(system_logs),
        "logs": system_logs[-50:],  # 最新50件を返す
        "timestamp": datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)