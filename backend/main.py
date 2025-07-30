from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import json
import uuid
import base64
import re
from datetime import datetime
from typing import Dict, List, Optional
from io import BytesIO
from dotenv import load_dotenv
from PIL import Image
import serpapi
import httpx
from bs4 import BeautifulSoup

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

# 公式ドメインリスト（ハードコード）
OFFICIAL_DOMAINS = [
    # 日本の出版社・書店
    'amazon.com', 'amazon.co.jp', 'rakuten.co.jp', 'bookwalker.jp',
    'kadokawa.co.jp', 'shogakukan.co.jp', 'kodansha.co.jp',
    'shueisha.co.jp', 'akitashoten.co.jp', 'hakusensha.co.jp',
    'square-enix.co.jp', 'enterbrain.co.jp', 'futabasha.co.jp',
    'houbunsha.co.jp', 'mag-garden.co.jp', 'shinchosha.co.jp',

    # 海外の出版社・書店
    'viz.com', 'crunchyroll.com', 'funimation.com',
    'comixology.com', 'marvel.com', 'dc.com',
    'darkhorse.com', 'imagecomics.com', 'idwpublishing.com',

    # 電子書籍プラットフォーム
    'kindle.amazon.com', 'kobo.rakuten.co.jp', 'ebookjapan.yahoo.co.jp',
    'cmoa.jp', 'booklive.jp', 'honto.jp', 'tsutaya.tsite.jp',

    # 公式サイト例
    'publisher.co.jp', 'official-site.com'
]

# 悪用判定キーワードリスト
SUSPICIOUS_KEYWORDS = [
    # 日本語キーワード
    '無料ダウンロード', '違法', 'コピー', '海賊版', 'パイレーツ',
    '無断転載', '著作権侵害', 'crack', 'torrent', 'アップロード',
    'リーク', 'ネタバレ', '先行公開', '非公式', 'ファンサイト',

    # 英語キーワード
    'free download', 'illegal', 'piracy', 'pirate', 'copyright infringement',
    'unauthorized', 'leaked', 'ripped', 'cracked', 'bootleg',
    'fansite', 'fan translation', 'scanlation', 'raw manga'
]

# 危険キーワードリスト
DANGER_KEYWORDS = [
    'torrent', 'magnet', 'ダウンロード違法', '海賊版配布',
    'copyright violation', 'stolen content', 'illegal distribution'
]

def validate_image_file(file: UploadFile) -> bool:
    """アップロードされたファイルが有効な画像かどうかを検証"""
    allowed_types = ["image/jpeg", "image/png", "image/jpg", "image/gif", "image/webp"]
    return file.content_type in allowed_types

def encode_image_to_base64(image_path: str) -> str:
    """画像ファイルをBase64エンコードする"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

async def check_domain_and_analyze(url: str, domain: str) -> Dict[str, str]:
    """ドメインを分析し、必要に応じてHTMLを取得して内容を分析する"""

    # 公式ドメインチェック
    is_official = any(official_domain in domain.lower() for official_domain in OFFICIAL_DOMAINS)

    if is_official:
        return {
            "status": "safe",
            "reason": "公式ドメインです",
            "content_analysis": None
        }

    # 非公式の場合、HTMLを取得して分析
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            # User-Agentを設定してアクセス
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }

            response = await client.get(url, headers=headers)
            response.raise_for_status()

            # HTMLをパース
            soup = BeautifulSoup(response.text, 'html.parser')

            # JavaScriptやCSSを除去
            for script in soup(["script", "style"]):
                script.decompose()

            # テキスト内容を抽出
            text_content = soup.get_text()

            # 改行や空白を整理
            lines = (line.strip() for line in text_content.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = ' '.join(chunk for chunk in chunks if chunk)

            # テキスト内容を制限（最初の2000文字）
            text = text[:2000]

            # X(Twitter)の特別処理
            if 'twitter.com' in domain or 'x.com' in domain:
                return await analyze_twitter_content(text, url)

            # 悪用判定
            return analyze_content_for_violations(text, domain)

    except httpx.TimeoutException:
        return {
            "status": "unknown",
            "reason": "サイトへのアクセスがタイムアウトしました",
            "content_analysis": None
        }
    except httpx.HTTPStatusError as e:
        return {
            "status": "unknown",
            "reason": f"HTTP エラー: {e.response.status_code}",
            "content_analysis": None
        }
    except Exception as e:
        return {
            "status": "unknown",
            "reason": f"分析中にエラーが発生しました: {str(e)}",
            "content_analysis": None
        }

async def analyze_twitter_content(text: str, url: str) -> Dict[str, str]:
    """Twitter/X投稿の内容を分析する"""
    text_lower = text.lower()

    # Twitter特有の悪用パターンをチェック
    twitter_suspicious_patterns = [
        'ダウンロードはこちら', 'download here', 'link in bio',
        'dm for link', 'リンクは dm で', '詳細は dm',
        'free manga', 'フリー漫画', '無料で読める'
    ]

    if any(pattern in text_lower for pattern in twitter_suspicious_patterns):
        return {
            "status": "suspicious",
            "reason": "Twitter投稿に疑わしい内容が含まれています",
            "content_analysis": f"投稿内容（一部）: {text[:200]}..."
        }

    # 通常の悪用判定
    return analyze_content_for_violations(text, 'twitter.com')

def analyze_content_for_violations(text: str, domain: str) -> Dict[str, str]:
    """テキスト内容から著作権侵害や悪用を判定する"""
    text_lower = text.lower()

    # 危険キーワードチェック（最優先）
    found_danger_keywords = [keyword for keyword in DANGER_KEYWORDS if keyword.lower() in text_lower]
    if found_danger_keywords:
        return {
            "status": "danger",
            "reason": f"危険なキーワードが検出されました: {', '.join(found_danger_keywords)}",
            "content_analysis": f"分析対象テキスト（一部）: {text[:300]}..."
        }

    # 疑わしいキーワードチェック
    found_suspicious_keywords = [keyword for keyword in SUSPICIOUS_KEYWORDS if keyword.lower() in text_lower]
    if found_suspicious_keywords:
        return {
            "status": "suspicious",
            "reason": f"疑わしいキーワードが検出されました: {', '.join(found_suspicious_keywords)}",
            "content_analysis": f"分析対象テキスト（一部）: {text[:300]}..."
        }

    # ドメインベースの判定
    if any(suspicious in domain for suspicious in ['free', 'download', 'torrent', 'pirate']):
        return {
            "status": "suspicious",
            "reason": "ドメイン名に疑わしい要素が含まれています",
            "content_analysis": None
        }

    # 安全と判定
    return {
        "status": "medium",
        "reason": "特に問題は検出されませんでした",
        "content_analysis": None
    }

def analyze_domain(url: str) -> tuple[str, bool, str]:
    """URLからドメインを抽出し、基本的な脅威レベルを評価（後方互換性のため残す）"""
    from urllib.parse import urlparse

    domain = urlparse(url).netloc.lower()
    is_official = any(official_domain in domain for official_domain in OFFICIAL_DOMAINS)

    # 基本的な脅威レベル評価
    if is_official:
        threat_level = "safe"
    elif any(dangerous in domain for dangerous in ['torrent', 'pirate', 'illegal']):
        threat_level = "danger"
    elif any(suspicious in domain for suspicious in ['free', 'download', 'manga', 'raw']):
        threat_level = "suspicious"
    elif domain.endswith('.com') or domain.endswith('.jp') or domain.endswith('.org'):
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
                    domain, is_official, basic_threat_level = analyze_domain(url)

                    # 詳細分析を実行
                    try:
                        detailed_analysis = await check_domain_and_analyze(url, domain)
                    except Exception as e:
                        print(f"詳細分析エラー ({url}): {e}")
                        detailed_analysis = {
                            "status": "unknown",
                            "reason": f"分析エラー: {str(e)}",
                            "content_analysis": None
                        }

                    processed_results.append({
                        "url": url,
                        "domain": domain,
                        "title": title,
                        "source": source,
                        "is_official": is_official,
                        "threat_level": basic_threat_level,
                        "detailed_analysis": detailed_analysis,
                        "thumbnail": item.get("thumbnail", ""),
                        "analysis_timestamp": datetime.now().isoformat()
                    })

        # テキスト検索結果も処理
        if "inline_images" in results:
            for item in results["inline_images"][:5]:  # 上位5件
                url = item.get("link", "")
                title = item.get("title", "")
                source = item.get("source", "")

                if url and title:
                    domain, is_official, basic_threat_level = analyze_domain(url)

                    # 詳細分析を実行
                    try:
                        detailed_analysis = await check_domain_and_analyze(url, domain)
                    except Exception as e:
                        print(f"詳細分析エラー ({url}): {e}")
                        detailed_analysis = {
                            "status": "unknown",
                            "reason": f"分析エラー: {str(e)}",
                            "content_analysis": None
                        }

                    processed_results.append({
                        "url": url,
                        "domain": domain,
                        "title": title,
                        "source": source,
                        "is_official": is_official,
                        "threat_level": basic_threat_level,
                        "detailed_analysis": detailed_analysis,
                        "thumbnail": item.get("thumbnail", ""),
                        "analysis_timestamp": datetime.now().isoformat()
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

        # 結果を詳細分析ステータス別に分類
    safe_results = []
    suspicious_results = []
    danger_results = []
    medium_results = []
    unknown_results = []

    for r in results:
        detailed_status = r.get("detailed_analysis", {}).get("status", "unknown")

        if r["is_official"] or detailed_status == "safe":
            safe_results.append(r)
        elif detailed_status == "danger":
            danger_results.append(r)
        elif detailed_status == "suspicious":
            suspicious_results.append(r)
        elif detailed_status == "medium":
            medium_results.append(r)
        else:
            unknown_results.append(r)

    # 脅威レベルの統計
    threat_analysis = {
        "safe_sources": len(safe_results),
        "suspicious_sources": len(suspicious_results),
        "danger_sources": len(danger_results),
        "medium_risk": len(medium_results),
        "unknown_sources": len(unknown_results),
        "official_sources": len([r for r in results if r["is_official"]]),
        "analyzed_sources": len([r for r in results if r.get("detailed_analysis", {}).get("content_analysis")])
    }

    return {
        "success": True,
        "image_id": image_id,
        "total_results": len(results),
        "analysis": threat_analysis,
        "results": {
            "all": results,
            "safe": safe_results,
            "suspicious": suspicious_results,
            "danger": danger_results,
            "medium_risk": medium_results,
            "unknown": unknown_results
        },
        "detailed_analysis_summary": {
            "has_danger_sources": len(danger_results) > 0,
            "has_suspicious_sources": len(suspicious_results) > 0,
            "risk_level": "high" if danger_results else "medium" if suspicious_results else "low"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)