from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import gc
import os
import json
import uuid
import re
import logging
from datetime import datetime
from typing import Dict, List, Optional
from io import BytesIO, StringIO
from dotenv import load_dotenv
from PIL import Image
import httpx
from bs4 import BeautifulSoup
from google.cloud import vision
import google.generativeai as genai
import hashlib
import csv
from urllib.parse import urlparse
from fastapi.responses import Response

# ログ設定（最初に設定）
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# SerpAPI統合用インポート
try:
    import imagehash
    import requests
    from serpapi import GoogleSearch
    SERPAPI_SUPPORT = True
    logger.info("✅ SerpAPI機能が利用可能です")
except ImportError:
    SERPAPI_SUPPORT = False
    logger.warning("⚠️ SerpAPI関連ライブラリが見つかりません。pip install google-search-results imagehash を実行してください")

# PDF処理用ライブラリ
try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
    logger.info("✅ PDF処理機能が利用可能です (PyMuPDF)")
except ImportError:
    PDF_SUPPORT = False
    logger.warning("⚠️ PDF処理ライブラリが見つかりません。pip install PyMuPDF を実行してください")

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
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")
SERP_API_KEY = os.getenv("SERPAPI_KEY")

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
if not X_BEARER_TOKEN:
    missing_keys.append("X_BEARER_TOKEN (Twitter内容取得用)")
if not SERP_API_KEY:
    missing_keys.append("SERPAPI_KEY (SerpAPI逆画像検索用)")

if missing_keys:
    required_missing = [k for k in missing_keys if "精度向上用" not in k and "オプション" not in k]
    if required_missing:
        print(f"警告: 以下の必須環境変数が設定されていません: {', '.join(required_missing)}")
    optional_missing = [k for k in missing_keys if "精度向上用" in k or "オプション" in k or "Twitter内容取得用" in k]
    if optional_missing:
        print(f"情報: 以下のオプション環境変数が設定されていません: {', '.join(optional_missing)}")
    print("完全な機能を使用するには、.envファイルで以下を設定してください:")
    print("- GEMINI_API_KEY: Gemini AI用")
    print("- GOOGLE_APPLICATION_CREDENTIALS: Google Vision API用サービスアカウントキー")
    print("- X_BEARER_TOKEN: X API用（Twitter内容取得）")
    print("- SERPAPI_KEY: SerpAPI用（逆画像検索）")
else:
    print("✓ 必要なAPI_KEYが正常に設定されています")



# CORS設定 - 本番環境対応
allowed_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:5174",
    "https://fujisan-leak-detector.onrender.com",  # Render フロントエンド URL（予備）
    "https://fujisan-leak-detector-1.onrender.com",  # 実際のフロントエンド URL
]

# 環境変数でCORSオリジンを追加可能
if cors_origins := os.getenv("CORS_ORIGINS"):
    allowed_origins.extend(cors_origins.split(","))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
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
search_results: Dict[str, Dict] = {}

# JSONファイルでの永続化
RECORDS_FILE = "upload_records.json"
HISTORY_FILE = "history.json"

# メモリ内履歴データストレージ
analysis_history: List[Dict] = []

# バッチ処理状況管理
batch_jobs: Dict[str, Dict] = {}

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

def load_history():
    """履歴ファイルから履歴を読み込み"""
    global analysis_history
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                analysis_history = json.load(f)
                logger.info(f"📚 履歴読み込み完了: {len(analysis_history)}件")
    except Exception as e:
        logger.error(f"履歴の読み込みに失敗: {e}")
        analysis_history = []

def save_history():
    """履歴ファイルに履歴を保存"""
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(analysis_history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"履歴の保存に失敗: {e}")

def generate_search_method_summary(raw_urls: list) -> dict:
    """検索方法別の統計情報を生成（3つの取得経路版）"""
    summary = {
        "完全一致": 0,
        "部分一致": 0,
        "Google Lens完全一致": 0,
        "不明": 0
    }

    for url_data in raw_urls:
        if isinstance(url_data, dict):
            search_method = url_data.get("search_method", "不明")

            # 検索方法を分類（3つの取得経路）
            if search_method == "完全一致":
                summary["完全一致"] += 1
            elif search_method == "部分一致":
                summary["部分一致"] += 1
            elif search_method == "Google Lens完全一致":
                summary["Google Lens完全一致"] += 1
            else:
                summary["不明"] += 1
        else:
            summary["不明"] += 1

    return summary

def calculate_image_hash(image_content: bytes) -> str:
    """
    画像コンテンツからSHA-256ハッシュ値を計算
    同じ画像を識別するために使用
    """
    return hashlib.sha256(image_content).hexdigest()

def save_analysis_to_history(image_id: str, image_hash: str, results: List[Dict]):
    """
    分析結果を履歴に保存
    """
    global analysis_history

    if image_id not in upload_records:
        return

    upload_record = upload_records[image_id]

    history_entry = {
        "history_id": str(uuid.uuid4()),
        "image_id": image_id,
        "image_hash": image_hash,
        "original_filename": upload_record.get("original_filename", "不明"),
        "analysis_date": datetime.now().isoformat(),
        "analysis_timestamp": int(datetime.now().timestamp()),
        "found_urls_count": upload_record.get("found_urls_count", 0),
        "processed_results_count": len(results),
        "results": results
    }

    analysis_history.append(history_entry)
    save_history()
    logger.info(f"📚 履歴に保存: {image_id} ({len(results)}件の結果)")

def get_previous_analysis(image_hash: str, exclude_history_id: Optional[str] = None) -> Dict | None:
    """
    同じ画像ハッシュの過去の分析結果を取得（最新のもの）
    """
    matching_histories = [
        h for h in analysis_history
        if h.get("image_hash") == image_hash and h.get("history_id") != exclude_history_id
    ]

    if not matching_histories:
        return None

    # 最新の分析結果を返す
    return max(matching_histories, key=lambda x: x.get("analysis_timestamp", 0))

def calculate_diff(current_results: List[Dict], previous_results: List[Dict]) -> Dict:
    """
    現在の結果と過去の結果の差分を計算
    """
    # URLをキーとしてマップを作成
    current_urls = {r["url"]: r for r in current_results}
    previous_urls = {r["url"]: r for r in previous_results}

    # 新規URL（現在にあるが過去にない）
    new_urls = []
    for url in current_urls:
        if url not in previous_urls:
            new_urls.append(current_urls[url])

    # 消失URL（過去にあるが現在にない）
    disappeared_urls = []
    for url in previous_urls:
        if url not in current_urls:
            disappeared_urls.append(previous_urls[url])

    # 判定変更URL（両方にあるが判定が変わった）
    changed_urls = []
    for url in current_urls:
        if url in previous_urls:
            current_judgment = current_urls[url].get("judgment", "？")
            previous_judgment = previous_urls[url].get("judgment", "？")
            if current_judgment != previous_judgment:
                changed_urls.append({
                    "url": url,
                    "current": current_urls[url],
                    "previous": previous_urls[url]
                })

    return {
        "new_urls": new_urls,
        "disappeared_urls": disappeared_urls,
        "changed_urls": changed_urls,
        "has_changes": len(new_urls) > 0 or len(disappeared_urls) > 0 or len(changed_urls) > 0,
        "total_new": len(new_urls),
        "total_disappeared": len(disappeared_urls),
        "total_changed": len(changed_urls)
    }

# アプリ起動時に記録と履歴を読み込み
load_records()
load_history()

# 公式ドメインリストは削除（Gemini AIで動的判定）

# Vision APIクライアントをグローバルで初期化（Render対応）
try:
    import json
    from google.oauth2 import service_account

    # まず GOOGLE_APPLICATION_CREDENTIALS_JSON を確認
    google_credentials_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if google_credentials_json:
        credentials_info = json.loads(google_credentials_json)
        credentials = service_account.Credentials.from_service_account_info(credentials_info)
        vision_client = vision.ImageAnnotatorClient(credentials=credentials)
        logger.info("✅ Google Vision API認証完了（GOOGLE_APPLICATION_CREDENTIALS_JSON）")
    else:
        # GOOGLE_APPLICATION_CREDENTIALS の値を確認
        google_credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if google_credentials:
            # JSON文字列かファイルパスかを判定
            if google_credentials.strip().startswith('{'):
                # JSON文字列として処理
                credentials_info = json.loads(google_credentials)
                credentials = service_account.Credentials.from_service_account_info(credentials_info)
                vision_client = vision.ImageAnnotatorClient(credentials=credentials)
                logger.info("✅ Google Vision API認証完了（GOOGLE_APPLICATION_CREDENTIALS JSON形式）")
            else:
                # ファイルパスとして処理
                if os.path.exists(google_credentials):
                    vision_client = vision.ImageAnnotatorClient()
                    logger.info("✅ Google Vision API認証完了（ファイルパス）")
                else:
                    logger.warning(f"⚠️ 認証ファイルが見つかりません: {google_credentials}")
                    vision_client = None
        else:
            # デフォルト認証を試行
            vision_client = vision.ImageAnnotatorClient()
            logger.info("✅ Google Vision API認証完了（デフォルト認証）")
except Exception as e:
    logger.warning(f"⚠️ Google Vision API初期化失敗: {e}")
    vision_client = None

# Geminiモデルをグローバルで初期化
if GEMINI_API_KEY:
    try:
        gemini_model = genai.GenerativeModel('gemini-2.5-flash')
        logger.info("✅ Gemini モデル初期化完了")
        logger.info("✅ Gemini API設定確認完了")
    except Exception as e:
        logger.error(f"❌ Gemini モデル初期化失敗: {e}")
        gemini_model = None
else:
    logger.error("❌ GEMINI_API_KEY が設定されていません")
    gemini_model = None

def validate_file(file: UploadFile) -> bool:
    """アップロードされたファイルが有効な画像またはPDFかどうかを検証"""
    allowed_types = ["image/jpeg", "image/png", "image/jpg", "image/gif", "image/webp"]

    # PDF対応
    if PDF_SUPPORT:
        allowed_types.extend(["application/pdf"])

    return file.content_type in allowed_types

# 後方互換性のため
def validate_image_file(file: UploadFile) -> bool:
    """後方互換性のため残している（非推奨）"""
    return validate_file(file)

def convert_pdf_to_images(pdf_content: bytes) -> List[bytes]:
    """
    PDFファイルを画像のリストに変換する
    各ページを個別の画像として返す
    """
    images = []
    pdf_document = None

    try:
        # 方法1: PyMuPDF (fitz) を使用
        if 'fitz' in globals():
            logger.info("🔄 PyMuPDF でPDFを画像に変換中...")
            pdf_document = fitz.open(stream=pdf_content, filetype="pdf")
            page_count = pdf_document.page_count  # close前に取得
            logger.info(f"📄 PDF総ページ数: {page_count}")

            for page_num in range(page_count):
                page = pdf_document[page_num]
                # 高品質でPDFページを画像に変換 (PyMuPDF 1.26.3対応)
                pix = page.get_pixmap(dpi=200)  # type: ignore # DPIで品質指定
                img_data = pix.tobytes("png")
                images.append(img_data)
                logger.info(f"📄 ページ {page_num + 1} を画像に変換完了")

            return images

    except Exception as e:
        logger.warning(f"⚠️ PyMuPDF変換失敗: {e}")
        return []

    finally:
        # PDF文書を確実に閉じる
        if pdf_document is not None:
            try:
                pdf_document.close()
                logger.debug("🔒 PDF文書クローズ完了")
            except Exception as e:
                logger.warning(f"⚠️ PDF文書クローズ失敗: {e}")

        # メモリ最適化
        gc.collect()

    logger.error("❌ PDFを画像に変換できませんでした")
    return []

def extract_pdf_text(pdf_content: bytes) -> str:
    """
    PDFからテキストを抽出する（補助情報として使用）
    """
    pdf_document = None

    try:
        # 方法1: PyMuPDF (fitz) を使用
        if 'fitz' in globals():
            logger.info("🔄 PyMuPDF でテキスト抽出中...")
            pdf_document = fitz.open(stream=pdf_content, filetype="pdf")
            text = ""
            page_count = pdf_document.page_count  # close前に取得

            for page_num in range(page_count):
                page = pdf_document[page_num]
                page_text = page.get_text()  # type: ignore
                text += f"[ページ {page_num + 1}]\n{page_text}\n\n"

            return text.strip()

    except Exception as e:
        logger.warning(f"⚠️ PyMuPDF テキスト抽出失敗: {e}")
        return ""

    finally:
        # PDF文書を確実に閉じる
        if pdf_document is not None:
            try:
                pdf_document.close()
                logger.debug("🔒 PDFテキスト抽出: 文書クローズ完了")
            except Exception as e:
                logger.warning(f"⚠️ PDFテキスト抽出: 文書クローズ失敗: {e}")

        # メモリ最適化
        gc.collect()

    return ""

def is_pdf_file(content_type: str, filename: str = "") -> bool:
    """ファイルがPDFかどうかを判定"""
    return (content_type == "application/pdf" or
            bool(filename and filename.lower().endswith('.pdf')))

# Base64エンコード関数は削除（不要）

def validate_url_availability(url: str) -> bool:
    """
    URLの有効性を事前にチェックする（HEADリクエスト）
    200番台のステータスコードの場合のみTrueを返す
    """
    try:
        with httpx.Client(timeout=5.0, follow_redirects=True) as client:
            response = client.head(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            return 200 <= response.status_code < 300
    except Exception as e:
        logger.warning(f"⚠️ URL有効性チェック失敗 {url}: {e}")
        return False

def is_reliable_domain(url: str) -> bool:
    """
    ドメインが信頼できるかどうかをチェックする
    疑わしい画像ホスティングサービスや怪しいドメインを除外
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # 除外すべき画像ホスティング/CDNドメイン
        excluded_domains = [
            'pbs.twimg.com',
            'm.media-amazon.com',
            'img-cdn.theqoo.net',
            'i.imgur.com',
            'cdn.discordapp.com',
            'media.discordapp.net',
            'images.unsplash.com',
            'cdn.pixabay.com',
            'images.pexels.com',
            'img.freepik.com',
            'thumbs.dreamstime.com',
            'previews.123rf.com',
            'st.depositphotos.com',
            'c8.alamy.com',
            'media.gettyimages.com',
            'us.123rf.com',
            'image.shutterstock.com',
            't3.ftcdn.net',
            't4.ftcdn.net',
            'static.turbosquid.com',
            'render.fineartamerica.com'
        ]

        # 除外ドメインチェック
        for excluded in excluded_domains:
            if excluded in domain:
                logger.info(f"⏭️ 除外ドメインのためスキップ: {domain}")
                return False

        # 極端に短いドメイン名を除外（怪しいドメインの可能性）
        if len(domain.replace('.', '')) < 5:
            logger.info(f"⏭️ 短すぎるドメインのためスキップ: {domain}")
            return False

        # 数字のみのサブドメインを除外
        if any(part.isdigit() for part in domain.split('.')):
            logger.info(f"⏭️ 数字サブドメインのためスキップ: {domain}")
            return False

        return True
    except Exception as e:
        logger.warning(f"⚠️ ドメイン信頼性チェック失敗 {url}: {e}")
        return False

def estimate_urls_from_text(detected_text: str, confidence_score: float) -> list[dict]:
    """
    テキスト検出結果から関連URLを推定する
    """
    estimated_urls = []

    # テキストとURLのマッピング辞書（大幅拡張）
    text_to_urls = {
        # ブランド名
        'apple': ['https://www.apple.com'],
        'google': ['https://www.google.com'],
        'microsoft': ['https://www.microsoft.com'],
        'amazon': ['https://www.amazon.com'],
        'toyota': ['https://www.toyota.com'],
        'honda': ['https://www.honda.com'],
        'sony': ['https://www.sony.com'],
        'nintendo': ['https://www.nintendo.com'],
        'starbucks': ['https://www.starbucks.com'],
        'mcdonalds': ['https://www.mcdonalds.com'],

        # 一般的なキーワード
        'iphone': ['https://www.apple.com'],
        'android': ['https://www.google.com'],
        'windows': ['https://www.microsoft.com'],
        'playstation': ['https://www.playstation.com'],
        'xbox': ['https://www.microsoft.com'],

        # 日本のブランド
        'ドコモ': ['https://www.docomo.ne.jp'],
        'au': ['https://www.au.com'],
        'softbank': ['https://www.softbank.jp'],
        'セブンイレブン': ['https://www.7-eleven.co.jp'],
        'ローソン': ['https://www.lawson.co.jp'],
        'ファミマ': ['https://www.family.co.jp'],

        # 日本の人名・芸能人（逆検索対象）
        '前島': ['https://www.google.com/search?q=前島亜美', 'https://seigura.com', 'https://natalie.mu'],
        '亜美': ['https://www.google.com/search?q=前島亜美', 'https://seigura.com', 'https://natalie.mu'],
        '前島亜美': ['https://www.google.com/search?q=前島亜美', 'https://seigura.com', 'https://natalie.mu'],
        'まえしま': ['https://www.google.com/search?q=前島亜美', 'https://seigura.com'],
        'あみ': ['https://www.google.com/search?q=前島亜美', 'https://seigura.com'],
        'maeshima': ['https://www.google.com/search?q=前島亜美', 'https://seigura.com'],
        'ami': ['https://www.google.com/search?q=前島亜美', 'https://seigura.com'],

        # 作品名・タイトル
        '公女': ['https://www.google.com/search?q=公女殿下の家庭教師', 'https://seigura.com'],
        '殿下': ['https://www.google.com/search?q=公女殿下の家庭教師', 'https://seigura.com'],
        '家庭教師': ['https://www.google.com/search?q=公女殿下の家庭教師', 'https://seigura.com'],
        'カレン': ['https://www.google.com/search?q=公女殿下の家庭教師+カレン', 'https://seigura.com'],
        'karen': ['https://www.google.com/search?q=公女殿下の家庭教師+カレン', 'https://seigura.com'],

        # 音楽関連
        'wish': ['https://www.google.com/search?q=Wish+for+you', 'https://natalie.mu', 'https://www.oricon.co.jp'],
        'アミュレット': ['https://www.google.com/search?q=アミュレット+前島亜美', 'https://natalie.mu'],
        '劇薬': ['https://www.google.com/search?q=劇薬+前島亜美', 'https://natalie.mu'],
        'amulet': ['https://www.google.com/search?q=アミュレット+前島亜美', 'https://natalie.mu'],

        # 声優・アニメ関連の詳細
        'bang': ['https://www.google.com/search?q=BanG+Dream', 'https://seigura.com'],
        'dream': ['https://www.google.com/search?q=BanG+Dream', 'https://seigura.com'],
        'bangdream': ['https://www.google.com/search?q=BanG+Dream', 'https://seigura.com'],
        'ぱすてる': ['https://www.google.com/search?q=ぱすてるらいふ', 'https://seigura.com'],
        'らいふ': ['https://www.google.com/search?q=ぱすてるらいふ', 'https://seigura.com'],
        'プリティ': ['https://www.google.com/search?q=プリティリズム', 'https://seigura.com'],
        'リズム': ['https://www.google.com/search?q=プリティリズム', 'https://seigura.com'],
        'オーロラ': ['https://www.google.com/search?q=プリティリズム+オーロラドリーム', 'https://seigura.com'],
        'ドリーム': ['https://www.google.com/search?q=プリティリズム+オーロラドリーム', 'https://seigura.com'],
        '古見': ['https://www.google.com/search?q=古見さんは+コミュ症です', 'https://seigura.com'],
        'コミュ': ['https://www.google.com/search?q=古見さんは+コミュ症です', 'https://seigura.com'],
        '症': ['https://www.google.com/search?q=古見さんは+コミュ症です', 'https://seigura.com'],
        'アサルト': ['https://www.google.com/search?q=アサルトリリィ', 'https://seigura.com'],
        'リリィ': ['https://www.google.com/search?q=アサルトリリィ', 'https://seigura.com'],
        'bouquet': ['https://www.google.com/search?q=アサルトリリィ+BOUQUET', 'https://seigura.com'],

        # 日付・時間関連
        '11月': ['https://www.google.com/search?q=11月22日+前島亜美', 'https://seigura.com'],
        '22日': ['https://www.google.com/search?q=11月22日+前島亜美', 'https://seigura.com'],
        '生まれ': ['https://www.google.com/search?q=前島亜美+誕生日', 'https://seigura.com'],
        '誕生': ['https://www.google.com/search?q=前島亜美+誕生日', 'https://seigura.com'],

        # 業界・職業関連
        'ボイス': ['https://www.google.com/search?q=ボイスキット', 'https://seigura.com'],
        'キット': ['https://www.google.com/search?q=ボイスキット', 'https://seigura.com'],
        '所属': ['https://www.google.com/search?q=ボイスキット+所属', 'https://seigura.com'],

        # 一般的な日本語キーワード
        '歌': ['https://www.google.com/search?q=歌手'],
        '楽曲': ['https://www.google.com/search?q=楽曲'],
        '音楽': ['https://www.google.com/search?q=音楽'],
        'ライブ': ['https://www.google.com/search?q=ライブ'],
        'コンサート': ['https://www.google.com/search?q=コンサート'],

        # 声優・アニメ関連
        '声優': ['https://www.google.com/search?q=声優'],
        'アニメ': ['https://www.google.com/search?q=アニメ'],
        'キャラクター': ['https://www.google.com/search?q=キャラクター'],
        'ボイス': ['https://www.google.com/search?q=ボイス'],

        # メディア・出版関連
        '雑誌': ['https://www.google.com/search?q=雑誌'],
        '記事': ['https://www.google.com/search?q=記事'],
        'インタビュー': ['https://www.google.com/search?q=インタビュー'],
        '取材': ['https://www.google.com/search?q=取材'],
    }

    # テキストの小文字化
    text_lower = detected_text.lower()

    # マッピング辞書から関連URLを検索
    for keyword, urls in text_to_urls.items():
        if keyword.lower() in text_lower:
            for url in urls:
                # 信頼度に基づいて分類
                if confidence_score >= 0.9:
                    search_method = "高信頼度テキスト"
                    confidence = "高"
                elif confidence_score >= 0.7:
                    search_method = "中信頼度テキスト"
                    confidence = "中"
                else:
                    search_method = "低信頼度テキスト"
                    confidence = "低"

                estimated_urls.append({
                    "url": url,
                    "search_method": search_method,
                    "search_source": "Text Detection",
                    "score": confidence_score,
                    "confidence": confidence,
                    "detected_text": detected_text
                })

    return estimated_urls

def reverse_search_from_detected_urls(detected_urls: list[dict]) -> list[dict]:
    """
    検出されたURLから逆検索を行い、関連URLを発見する
    """
    reverse_results = []

    logger.info("🔄 逆検索機能開始...")

    for url_data in detected_urls:
        original_url = url_data.get("url", "")

        # Google検索URLの場合、検索クエリを抽出して関連サイトを推定
        if "google.com/search" in original_url:
            try:
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(original_url)
                query_params = parse_qs(parsed.query)
                search_query = query_params.get('q', [''])[0]

                if search_query:
                    logger.info(f"🔍 逆検索クエリ発見: {search_query}")

                    # 検索クエリに基づいて関連サイトを推定
                    related_urls = estimate_related_sites_from_query(search_query)

                    for related_url in related_urls:
                        reverse_results.append({
                            "url": related_url,
                            "search_method": "逆引き検索",
                            "search_source": "Reverse Search",
                            "score": 0.7,
                            "confidence": "中",
                            "original_query": search_query
                        })
                        logger.info(f"  ✅ 逆検索結果追加: {related_url}")

            except Exception as e:
                logger.warning(f"⚠️ 逆検索処理エラー: {e}")

    logger.info(f"✅ 逆検索完了: {len(reverse_results)}件の関連URL発見")
    return reverse_results

def estimate_related_sites_from_query(search_query: str) -> list[str]:
    """
    検索クエリから関連サイトを推定する
    """
    related_sites = []
    query_lower = search_query.lower()

    # クエリベースの関連サイト推定
    site_mappings = {
        # 人名・芸能人関連
        '前島亜美': [
            'https://www.oricon.co.jp',
            'https://natalie.mu',
            'https://www.animenewsnetwork.com',
            'https://seigura.com',
            'https://www.famitsu.com'
        ],
        '声優': [
            'https://seigura.com',
            'https://www.animenewsnetwork.com',
            'https://natalie.mu',
            'https://www.oricon.co.jp'
        ],
        '音楽': [
            'https://natalie.mu',
            'https://www.oricon.co.jp',
            'https://www.billboard-japan.com'
        ],
        'アニメ': [
            'https://www.animenewsnetwork.com',
            'https://natalie.mu',
            'https://www.famitsu.com'
        ],
        'ゲーム': [
            'https://www.famitsu.com',
            'https://www.4gamer.net',
            'https://natalie.mu'
        ]
    }

    # 部分マッチングで関連サイトを検索
    for keyword, sites in site_mappings.items():
        if keyword in query_lower:
            related_sites.extend(sites)

    # 重複除去
    return list(set(related_sites))

def cleanup_old_temp_files():
    """
    古いGoogle Lens一時ファイルをクリーンアップ（1時間以上前のファイル）
    """
    try:
        import time
        current_time = time.time()
        cutoff_time = current_time - 3600  # 1時間前
        
        if not os.path.exists(UPLOAD_DIR):
            return
        
        cleaned_count = 0
        for filename in os.listdir(UPLOAD_DIR):
            if filename.startswith("google_lens_temp_"):
                file_path = os.path.join(UPLOAD_DIR, filename)
                try:
                    # ファイル名からタイムスタンプ抽出
                    timestamp_str = filename.split("_")[3]  # google_lens_temp_{timestamp}_{uuid}
                    file_timestamp = int(timestamp_str)
                    
                    if file_timestamp < cutoff_time:
                        os.remove(file_path)
                        cleaned_count += 1
                        logger.debug(f"🧹 古い一時ファイル削除: {filename}")
                except (ValueError, IndexError, OSError) as e:
                    logger.warning(f"⚠️ 一時ファイルクリーンアップエラー {filename}: {e}")
        
        if cleaned_count > 0:
            logger.info(f"🧹 一時ファイルクリーンアップ完了: {cleaned_count}件削除")
    except Exception as e:
        logger.warning(f"⚠️ 一時ファイルクリーンアップ失敗: {e}")

def calculate_multi_hash_similarity(image1: Image.Image, image2: Image.Image) -> Dict:
    """
    複数のハッシュアルゴリズムを使用して画像の類似度を計算
    より高精度な「完全一致」判定を実現
    """
    try:
        # 複数のハッシュアルゴリズムで比較
        phash_dist = imagehash.phash(image1) - imagehash.phash(image2)
        dhash_dist = imagehash.dhash(image1) - imagehash.dhash(image2)
        ahash_dist = imagehash.average_hash(image1) - imagehash.average_hash(image2)

        # 総合スコア計算（全てのハッシュが低距離の場合のみ高スコア）
        total_distance = phash_dist + dhash_dist + ahash_dist
        max_distance = max(phash_dist, dhash_dist, ahash_dist)

        return {
            "phash_distance": int(phash_dist),
            "dhash_distance": int(dhash_dist),
            "ahash_distance": int(ahash_dist),
            "total_distance": int(total_distance),
            "max_distance": int(max_distance),
            "is_near_exact": phash_dist <= 2 and dhash_dist <= 3 and ahash_dist <= 3 and max_distance <= 3,
            "similarity_score": max(0, 1.0 - (total_distance / 30.0))  # 30は経験的な最大値
        }
    except Exception as e:
        logger.warning(f"⚠️ ハッシュ計算エラー: {e}")
        return {
            "phash_distance": 999,
            "dhash_distance": 999,
            "ahash_distance": 999,
            "total_distance": 999,
            "max_distance": 999,
            "is_near_exact": False,
            "similarity_score": 0.0
        }


def google_lens_exact_search(input_image_bytes: bytes) -> List[Dict]:
    """
    SerpAPI Google Lens Exact Matches APIで完全一致画像を取得

    Args:
        input_image_bytes (bytes): 入力画像のバイトデータ

    Returns:
        List[Dict]: Google Lens完全一致のURLリスト
    """
    if not SERP_API_KEY or not SERPAPI_SUPPORT:
        logger.warning("⚠️ SerpAPI機能が利用できません")
        return []

    temp_file_path = None
    try:
        logger.info("🔍 Google Lens Exact Matches API検索開始")

        # 1. 入力画像の前処理
        try:
            input_image = Image.open(BytesIO(input_image_bytes))
            if input_image.mode != 'RGB':
                input_image = input_image.convert('RGB')

            # 画像品質チェック
            width, height = input_image.size
            if width < 50 or height < 50:
                logger.warning("⚠️ 入力画像が小さすぎます（50x50未満）")
                return []

            logger.info(f"📊 入力画像解析: サイズ={width}x{height}")

        except Exception as e:
            logger.error(f"❌ 入力画像処理エラー: {e}")
            return []

        # 2. 永続化一時ファイル作成（ワーカー再起動対応）
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        
        # タイムスタンプ付きファイル名（クリーンアップ用）
        import time
        timestamp = int(time.time())
        temp_filename = f"google_lens_temp_{timestamp}_{uuid.uuid4().hex[:8]}.jpg"
        temp_file_path = os.path.join(UPLOAD_DIR, temp_filename)
        logger.info(f"📁 永続化一時ファイル作成予定: {temp_file_path}")
        
        # 古い一時ファイルのクリーンアップ（1時間以上前）
        cleanup_old_temp_files()

        # 高品質でJPEG保存（Google Lens APIの精度向上のため）
        input_image.save(temp_file_path, 'JPEG', quality=95, optimize=False)
        logger.info(f"💾 一時ファイル作成完了: {temp_file_path} ({os.path.getsize(temp_file_path)} bytes)")

        # ファイル存在確認
        if not os.path.exists(temp_file_path):
            logger.error(f"❌ 一時ファイル作成失敗: {temp_file_path}")
            return []

        # 3. 一時ファイルをHTTPで公開（Render対応）
        render_url = os.getenv("RENDER_EXTERNAL_URL")
        if render_url:
            # Render本番環境の場合
            base_url = render_url.rstrip('/')
            logger.info(f"🌐 Render環境使用: {base_url}")
        else:
            # ローカル開発環境の場合
            base_url = os.getenv("VITE_API_BASE_URL", "http://localhost:8000")
            logger.info(f"🏠 ローカル環境使用: {base_url}")

        image_url = f"{base_url}/uploads/{temp_filename}"
        logger.info(f"📁 一時画像URL: {image_url}")

        # 4. Google Lens Exact Matches API実行
        # ローカル環境では`image`パラメータ、本番環境では`url`パラメータを使用
        if render_url:
            # 本番環境（Render）: urlパラメータを使用
            search_params = {
                "engine": "google_lens",
                "type": "exact_matches",
                "url": image_url,  # 外部アクセス可能なURL
                "api_key": SERP_API_KEY,
                "no_cache": True,
                "safe": "off"
            }
            logger.info(f"🌐 本番環境 - URL使用: {image_url}")
        else:
            # ローカル環境: imageパラメータを使用
            search_params = {
                "engine": "google_lens",
                "type": "exact_matches",
                "image": temp_file_path,  # ローカルファイルパス
                "api_key": SERP_API_KEY,
                "no_cache": True,
                "safe": "off"
            }
            logger.info(f"🏠 ローカル環境 - ファイルパス使用: {temp_file_path}")

        logger.info(f"🔍 Google Lens APIパラメータ: {search_params}")
        
        # SerpAPIリクエスト（タイムアウト対策）
        try:
            search = GoogleSearch(search_params)
            logger.info("🌐 SerpAPI Google Lens リクエスト実行中...")
            
            # タイムアウト付きでリクエスト実行
            import signal
            import threading
            
            results = None
            exception_occurred = None
            
            def serpapi_request():
                nonlocal results, exception_occurred
                try:
                    results = search.get_dict()
                except Exception as e:
                    exception_occurred = e
            
            # タイムアウト付きリクエスト（120秒）
            thread = threading.Thread(target=serpapi_request)
            thread.daemon = True
            thread.start()
            thread.join(timeout=120)
            
            if thread.is_alive():
                logger.error("❌ SerpAPI リクエストタイムアウト (120秒)")
                logger.info("   📊 Google Vision APIの結果のみ使用します")
                return []
            
            if exception_occurred:
                raise exception_occurred
            
            if results is None:
                logger.error("❌ SerpAPI リクエスト結果が空")
                logger.info("   📊 Google Vision APIの結果のみ使用します")
                return []
            
            logger.info(f"📡 SerpAPI レスポンス受信: {type(results)} - キー: {list(results.keys()) if isinstance(results, dict) else 'Not a dict'}")
            
        except Exception as serpapi_error:
            logger.error(f"❌ SerpAPI リクエストエラー: {str(serpapi_error)}")
            logger.info("   📊 Google Vision APIの結果のみ使用します")
            return []

        # エラーチェック
        if "error" in results:
            error_msg = results["error"]
            logger.error(f"❌ SerpAPI Google Lens エラー: {error_msg}")

            # 特定のエラーの場合は詳細情報を提供
            if "hasn't returned any results" in error_msg:
                logger.info("💡 SerpAPI Google Lensで一致する結果が見つかりませんでした")
                logger.info("   ✅ これは正常な動作です（この画像に完全一致がない）")
                logger.info("   📊 Google Vision APIの結果を使用します")
                # エラーではなく、結果が無いだけなので空の配列を返す
                return []
            elif "quota" in error_msg.lower() or "limit" in error_msg.lower():
                logger.warning("⚠️ SerpAPI クォータ制限に達しました")
                logger.info("   📊 Google Vision APIの結果のみ使用します")
                return []
            elif "invalid" in error_msg.lower() or "parameter" in error_msg.lower():
                logger.error("❌ SerpAPI パラメータエラー - API設定を確認してください")
                return []
            elif "couldn't get valid results" in error_msg.lower():
                logger.warning("⚠️ SerpAPI 画像処理失敗 - 一時的な問題の可能性")
                logger.info("   💡 原因: 画像アクセス失敗、API負荷、ネットワーク問題")
                logger.info("   📊 Google Vision APIの結果のみ使用します")
                return []
            elif "timeout" in error_msg.lower() or "slow" in error_msg.lower():
                logger.warning("⚠️ SerpAPI タイムアウト - リクエスト処理時間超過")
                return []
            else:
                logger.error(f"❌ SerpAPI 不明なエラー: {error_msg}")
                logger.info("   📊 Google Vision APIの結果のみ使用します")
                return []

        # 5. exact_matchesを取得
        exact_matches = results.get("exact_matches", [])
        logger.info(f"🎯 Google Lens Exact Matchesから {len(exact_matches)} 件の候補を取得")

        # デバッグ: レスポンス全体をログ出力（機密情報を除く）
        if not exact_matches and "error" not in results:
            logger.warning(f"⚠️ Google Lens APIレスポンス詳細: {results}")
            # 他に使用可能なキーがあるかチェック
            for key in results.keys():
                if key != "api_key":  # API_KEYは出力しない
                    logger.info(f"   📋 レスポンスキー '{key}': {type(results[key])}")

        if not exact_matches:
            logger.info("💡 Google Lensで完全一致する画像が見つかりませんでした")
            return []

        # 6. exact_matchesを処理
        processed_results = []
        for i, match in enumerate(exact_matches):
            try:
                position = match.get("position", i + 1)
                title = match.get("title", "タイトルなし")
                source = match.get("source", "ソース不明")
                link = match.get("link", "")
                thumbnail = match.get("thumbnail", "")

                # 価格情報（商品の場合）
                price = match.get("price", "")
                extracted_price = match.get("extracted_price", 0)
                in_stock = match.get("in_stock", False)
                out_of_stock = match.get("out_of_stock", False)

                # 日付情報
                date = match.get("date", "")

                # 実際の画像サイズ
                actual_image_width = match.get("actual_image_width", 0)
                actual_image_height = match.get("actual_image_height", 0)

                if link:
                    result = {
                        "url": link,
                        "title": title,
                        "source": source,
                        "position": position,
                        "thumbnail": thumbnail,
                        "search_method": "Google Lens完全一致",
                        "search_source": "Google Lens Exact Matches",
                        "confidence": "high",  # Google Lensの完全一致は高信頼度
                        "score": 1.0,  # 完全一致なので最高スコア
                        "actual_image_width": actual_image_width,
                        "actual_image_height": actual_image_height
                    }

                    # 価格情報があれば追加
                    if price:
                        result["price"] = price
                        result["extracted_price"] = extracted_price
                        result["in_stock"] = in_stock
                        result["out_of_stock"] = out_of_stock

                    # 日付情報があれば追加
                    if date:
                        result["date"] = date

                    processed_results.append(result)
                    logger.info(f"✅ Google Lens完全一致 {position}: {title[:50]}...")

            except Exception as e:
                logger.debug(f"  ⚠️ Google Lens候補 {i+1} 処理エラー: {str(e)}")
                continue

        logger.info(f"✅ Google Lens検索完了: {len(processed_results)}件の完全一致を発見")

        return processed_results

    except Exception as e:
        logger.error(f"❌ Google Lens検索エラー: {str(e)}")
        return []

    finally:
        # 一時ファイル削除（SerpAPI完了後に遅延削除）
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                # 少し待ってからファイル削除（SerpAPIがアクセス完了するまで）
                import time
                time.sleep(1)  # 1秒待機
                os.remove(temp_file_path)
                logger.debug(f"🗑️ Google Lens一時ファイル削除: {temp_file_path}")
            except Exception as e:
                logger.warning(f"⚠️ Google Lens一時ファイル削除失敗: {str(e)}")
                # 削除失敗でも続行（次回起動時にクリーンアップされる）

def enhanced_image_search_with_reverse(image_content: bytes) -> list[dict]:
    """
    3つの取得経路による画像検索
    1. Google Vision API: 完全一致と部分一致のみ
    2. Google Lens API: 完全一致
    3. （textdetectionと逆引き検索は除去）
    """
    logger.info("🚀 3つの取得経路による画像検索開始")

    # 1. Google Vision API検索（完全一致と部分一致のみ）
    vision_results = search_web_for_image(image_content)

    # 2. Google Lens Exact Matches API検索
    google_lens_results = google_lens_exact_search(image_content)

    # 3. 結果を統合（重複URL除去、Google Lens優先）
    all_results = []

    # Google Lens結果を先に追加（優先度高）
    seen_urls = set()
    for result in google_lens_results:
        url = result.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            all_results.append(result)

    # Vision API結果を追加（重複チェック）
    for result in vision_results:
        url = result.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            all_results.append(result)

    logger.info(f"📊 画像検索結果統計:")
    logger.info(f"  - Google Vision API: {len(vision_results)}件（完全一致・部分一致）")
    logger.info(f"  - SerpAPI Google Lens: {len(google_lens_results)}件（完全一致）")
    logger.info(f"  - 重複除去後合計: {len(all_results)}件")

    return all_results

def search_web_for_image(image_content: bytes) -> list[dict]:
    """
    画像コンテンツを受け取り、Google Vision APIで
    同一画像が使用されているURLのリストを返す。
    各URLには検索方法（完全一致/部分一致/元記事検索）の分類情報を付与。
    """
    logger.info("🔍 画像検索開始（Vision API WEB+TEXT）")

    all_results = []

    try:
        # 1. Google Vision API
        logger.info("📊 【Phase 1】Google Vision API（WEB+TEXT）")

        # Vision APIクライアントが初期化されているかチェック
        if not vision_client:
            logger.error("❌ Google Vision APIクライアントが初期化されていません")
            logger.error("   設定確認: GOOGLE_APPLICATION_CREDENTIALS または GOOGLE_APPLICATION_CREDENTIALS_JSON")
            return []

        # 完全一致検出のための画像前処理最適化
        logger.info(f"🖼️ 画像サイズ: {len(image_content)} bytes")

        # 画像形式を確認
        try:
            from PIL import Image as PILImage
            import io

            pil_image = PILImage.open(io.BytesIO(image_content))
            logger.info(f"🖼️ 画像形式: {pil_image.format}, サイズ: {pil_image.size}, モード: {pil_image.mode}")

            # 完全一致検出のための最適サイズ調整
            min_dimension = 800  # 最小サイズを800pxに設定
            max_dimension = 4096  # 最大サイズを4Kに設定

            current_min = min(pil_image.size)
            current_max = max(pil_image.size)

            # 小さすぎる画像のアップスケーリング
            if current_min < min_dimension:
                scale_factor = min_dimension / current_min
                new_size = (int(pil_image.size[0] * scale_factor), int(pil_image.size[1] * scale_factor))
                pil_image = pil_image.resize(new_size, PILImage.Resampling.LANCZOS)
                logger.info(f"🔧 完全一致用アップスケーリング: {pil_image.size[0]}x{pil_image.size[1]} -> {new_size[0]}x{new_size[1]}")

                # アップスケーリング後の画像を保存
                output_buffer = io.BytesIO()
                if pil_image.mode in ('RGBA', 'LA', 'P'):
                    pil_image = pil_image.convert('RGB')
                pil_image.save(output_buffer, format='JPEG', quality=100, optimize=False, subsampling=0)
                image_content = output_buffer.getvalue()
                logger.info(f"🔧 アップスケーリング完了: {len(image_content)} bytes")

            # 大きすぎる画像のダウンスケーリング
            elif current_max > max_dimension:
                scale_factor = max_dimension / current_max
                new_size = (int(pil_image.size[0] * scale_factor), int(pil_image.size[1] * scale_factor))
                pil_image = pil_image.resize(new_size, PILImage.Resampling.LANCZOS)
                logger.info(f"🔧 完全一致用ダウンスケーリング: {pil_image.size[0]}x{pil_image.size[1]} -> {new_size[0]}x{new_size[1]}")

                # ダウンスケーリング後の画像を保存
                output_buffer = io.BytesIO()
                if pil_image.mode in ('RGBA', 'LA', 'P'):
                    pil_image = pil_image.convert('RGB')
                pil_image.save(output_buffer, format='JPEG', quality=100, optimize=False, subsampling=0)
                image_content = output_buffer.getvalue()
                logger.info(f"🔧 ダウンスケーリング完了: {len(image_content)} bytes")
            else:
                logger.info(f"✅ 画像サイズは完全一致検出に最適: {pil_image.size[0]}x{pil_image.size[1]}")

            # Vision API完全一致精度最適化（元画像優先）
            original_size = len(image_content)
            max_size = 10 * 1024 * 1024  # 10MBに拡大

            # 元の画像をそのまま試行（最高の完全一致精度のため）
            if original_size <= max_size:
                logger.info(f"✅ 元画像をそのまま使用（完全一致最優先）: {original_size} bytes")
            else:
                logger.info(f"🔧 画像サイズ最適化中... ({original_size} -> 目標: < {max_size})")

                # 完全一致検出のため可能な限り高解像度を維持
                max_dimension = 4096  # 4K解像度まで許可
                if max(pil_image.size) > max_dimension:
                    ratio = max_dimension / max(pil_image.size)
                    new_size = (int(pil_image.size[0] * ratio), int(pil_image.size[1] * ratio))
                    pil_image = pil_image.resize(new_size, PILImage.Resampling.LANCZOS)
                    logger.info(f"🔧 高解像度リサイズ完了: {new_size}")

                # 完全一致検出のため最高品質で保存
                output_buffer = io.BytesIO()
                if pil_image.mode in ('RGBA', 'LA', 'P'):
                    pil_image = pil_image.convert('RGB')

                # 完全一致検出に最適化された設定
                pil_image.save(output_buffer, format='JPEG', quality=100, optimize=False,
                                 subsampling=0, progressive=False)
                image_content = output_buffer.getvalue()
                logger.info(f"🔧 完全一致最適化完了: {len(image_content)} bytes")

            # PNG形式の場合、JPEG変換も試行（完全一致精度向上）
            if pil_image.format == 'PNG' and original_size <= max_size:
                logger.info(f"🔧 PNG->JPEG変換で完全一致精度向上を試行...")
                jpeg_buffer = io.BytesIO()
                rgb_image = pil_image
                if pil_image.mode in ('RGBA', 'LA', 'P'):
                    # 透明度を白背景で処理
                    rgb_image = PILImage.new('RGB', pil_image.size, (255, 255, 255))
                    if pil_image.mode == 'RGBA':
                        rgb_image.paste(pil_image, mask=pil_image.split()[-1])
                    else:
                        rgb_image.paste(pil_image)

                rgb_image.save(jpeg_buffer, format='JPEG', quality=100, optimize=False, subsampling=0)
                jpeg_content = jpeg_buffer.getvalue()

                # JPEGの方が小さい場合は採用
                if len(jpeg_content) < len(image_content):
                    image_content = jpeg_content
                    logger.info(f"🔧 JPEG変換採用: {len(image_content)} bytes")
                else:
                    logger.info(f"🔧 元PNG保持: {len(image_content)} bytes")

        except Exception as img_error:
            logger.warning(f"⚠️ 画像前処理エラー: {img_error}")

        image = vision.Image(content=image_content)

        # Vision API 検出実行（WEB_DETECTIONのみ）
        logger.info("🎯 Vision API 検出開始（WEB_DETECTION特化）")

        try:
            # WEB_DETECTION専用で最大精度を追求
            logger.info("🌐 WEB_DETECTION 実行中（最大結果数で精度重視）...")
            features = [
                vision.Feature(type_=vision.Feature.Type.WEB_DETECTION, max_results=2000)  # 最大結果数を増加
            ]
            request = vision.AnnotateImageRequest(image=image, features=features)
            response = vision_client.annotate_image(request=request)
            logger.info("✅ 検出完了")

            logger.info(f"📡 Vision API レスポンス受信完了")
            logger.info(f"📋 レスポンス詳細: type={type(response)}")
            if hasattr(response, 'error'):
                error_attr = getattr(response, 'error', None)
                logger.info(f"📋 エラー属性存在: {error_attr is not None}")
        except Exception as api_error:
            logger.error(f"❌ Vision API 呼び出しエラー: {api_error}")
            logger.error(f"   エラータイプ: {type(api_error).__name__}")

            # 具体的なエラー内容をチェック
            error_str = str(api_error).lower()
            if 'quota' in error_str or 'limit' in error_str:
                logger.error("   原因: APIクォータ制限に達している可能性があります")
            elif 'permission' in error_str or 'auth' in error_str:
                logger.error("   原因: 認証エラーまたは権限不足の可能性があります")
            elif 'billing' in error_str:
                logger.error("   原因: 課金設定に問題がある可能性があります")
            else:
                logger.error(f"   詳細: {api_error}")

            return []

        # レスポンスが正常か確認
        if not response:
            logger.error("❌ Vision API レスポンスが空です")
            return []

        # エラーチェック（エラーコードが0以外の場合のみエラーとして扱う）
        if hasattr(response, 'error') and response.error:
            # gRPC Status オブジェクトの詳細を取得
            error_code = getattr(response.error, 'code', 'UNKNOWN')
            error_message = getattr(response.error, 'message', '詳細不明')
            error_details = getattr(response.error, 'details', [])

            # エラーコードが0（OK）以外の場合のみエラーとして処理
            if error_code != 0:
                logger.error(f"❌ Vision API エラー:")
                logger.error(f"   コード: {error_code}")
                logger.error(f"   メッセージ: {error_message}")
                logger.error(f"   詳細: {error_details}")
                logger.error(f"   エラータイプ: {type(response.error)}")

                # エラーコードに基づく対処法の提示
                if error_code == 3:  # INVALID_ARGUMENT
                    logger.error("   原因: 無効な引数（画像形式や内容に問題がある可能性）")
                elif error_code == 7:  # PERMISSION_DENIED
                    logger.error("   原因: 権限不足（サービスアカウントの権限を確認してください）")
                elif error_code == 8:  # RESOURCE_EXHAUSTED
                    logger.error("   原因: リソース不足（APIクォータ制限に達している可能性）")
                elif error_code == 16:  # UNAUTHENTICATED
                    logger.error("   原因: 認証エラー（認証情報を確認してください）")

                return []
            else:
                logger.info(f"✅ Vision API レスポンス正常（エラーコード: {error_code}）")

        # WEB_DETECTION結果の存在チェック
        web_detection = response.web_detection if hasattr(response, 'web_detection') else None

        # WEB検出結果の件数を集計
        web_count = 0
        full_count = 0
        partial_count = 0
        similar_count = 0
        pages_count = 0

        if web_detection:
            full_count = len(web_detection.full_matching_images) if web_detection.full_matching_images else 0
            partial_count = len(web_detection.partial_matching_images) if web_detection.partial_matching_images else 0
            similar_count = len(web_detection.visually_similar_images) if web_detection.visually_similar_images else 0
            pages_count = len(web_detection.pages_with_matching_images) if web_detection.pages_with_matching_images else 0
            web_count = full_count + partial_count + similar_count

            # デバッグ情報: 類似画像が多いのに完全・部分一致が0件の場合
            if similar_count > 0 and full_count == 0 and partial_count == 0:
                logger.info(f"🔍 デバッグ: 類似画像{similar_count}件あり、完全・部分一致0件")
                logger.info("   - 画像の品質や解像度が影響している可能性があります")
                logger.info("   - または、この画像が非常に新しい/特殊な画像の可能性があります")

        logger.info(f"📈 Vision API検出結果（WEB_DETECTION特化、類似画像除外）:")
        logger.info(f"  - 完全一致画像: {full_count}件")
        logger.info(f"  - 部分一致画像: {partial_count}件")
        logger.info(f"  - 類似画像: {similar_count}件（スキップ）")
        logger.info(f"  - 関連ページ: {pages_count}件")
        logger.info(f"  - 有効検出: {full_count + partial_count + pages_count}件")

        # 1-1. WEB_DETECTION: 完全一致画像からURL収集
        if web_detection and web_detection.full_matching_images:
            logger.info(f"🎯 完全一致画像からURL抽出中... ({len(web_detection.full_matching_images)}件発見)")
            for i, img in enumerate(web_detection.full_matching_images):
                logger.info(f"   📋 完全一致画像 {i+1}: URL={getattr(img, 'url', 'なし')}, Score={getattr(img, 'score', 'なし')}")
                if img.url and img.url.startswith(('http://', 'https://')):
                    result = {
                        "url": img.url,
                        "search_method": "完全一致",
                        "search_source": "Vision API",
                        "score": getattr(img, 'score', 1.0),
                        "confidence": "高"
                    }
                    all_results.append(result)
                    logger.info(f"  ✅ 完全一致画像追加: {img.url}")

                    # seigura.comやNTTドコモの検出確認
                    if "seigura.com" in img.url.lower():
                        logger.info(f"  🎯 seigura.com検出成功！: {img.url}")
                    elif "ntt" in img.url.lower() or "docomo" in img.url.lower():
                        logger.info(f"  🎯 NTTドコモ検出成功！: {img.url}")
                else:
                    logger.warning(f"  ⚠️ 完全一致画像のURLが無効: {getattr(img, 'url', 'なし')}")
        else:
            logger.info("💡 完全一致画像が0件でした")

        # 1-2. WEB_DETECTION: 部分一致画像からURL収集（適応的スコア閾値）
        if web_detection and web_detection.partial_matching_images:
            logger.info(f"🎯 部分一致画像からURL抽出中... ({len(web_detection.partial_matching_images)}件発見)")

            # スコア分布をログ出力（デバッグ用）
            scores = [getattr(img, 'score', 0.0) for img in web_detection.partial_matching_images if img.url]
            if scores:
                max_score = max(scores)
                min_score = min(scores)
                avg_score = sum(scores) / len(scores)
                logger.info(f"  📊 部分一致スコア分布: 最高={max_score:.4f}, 最低={min_score:.4f}, 平均={avg_score:.4f}")

            # 適応的閾値設定（結果が0件にならないよう調整）
            adaptive_threshold = 0.01  # 基本閾値を大幅に下げる
            if scores and max(scores) < 0.05:
                adaptive_threshold = min_score  # 最低スコアでも採用
                logger.info(f"  🔧 適応的閾値適用: {adaptive_threshold:.4f} (全結果採用モード)")

            filtered_count = 0
            for i, img in enumerate(web_detection.partial_matching_images):
                if img.url and img.url.startswith(('http://', 'https://')):
                    score = getattr(img, 'score', 0.0)
                    logger.info(f"  🔍 部分一致候補 {i+1}: score={score:.4f}, url={img.url}")

                    if score >= adaptive_threshold:
                        img_confidence = "高" if score >= 0.5 else "中" if score >= 0.1 else "低"
                        img_result = {
                            "url": img.url,
                            "search_method": "部分一致",
                            "search_source": "Vision API",
                            "score": score,
                            "confidence": img_confidence
                        }
                        all_results.append(img_result)
                        logger.info(f"  ✅ 部分一致画像追加 (score: {score:.4f}): {img.url}")
                    else:
                        filtered_count += 1
                        logger.info(f"  ❌ スコア不足でスキップ (score: {score:.4f}): {img.url}")

            logger.info(f"  📊 部分一致結果: 採用={len(web_detection.partial_matching_images)-filtered_count}件, 除外={filtered_count}件")
        else:
            logger.info("💡 部分一致画像が0件でした")

        # 1-3. 類似画像は削除（使い物にならないため）
        if web_detection and web_detection.visually_similar_images:
            logger.info(f"⏭️ 類似画像をスキップ ({len(web_detection.visually_similar_images)}件発見、品質が低いため除外)")
        else:
            logger.info("💡 類似画像が0件でした")

        # 1-4. WEB_DETECTION: 関連ページからURL収集（適応的スコア閾値）
        if web_detection and web_detection.pages_with_matching_images:
            logger.info(f"🎯 関連ページからURL抽出中... ({len(web_detection.pages_with_matching_images)}件発見)")

            # スコア分布をログ出力（デバッグ用）
            page_scores = [getattr(page, 'score', 0.0) for page in web_detection.pages_with_matching_images if page.url]
            if page_scores:
                max_score = max(page_scores)
                min_score = min(page_scores)
                avg_score = sum(page_scores) / len(page_scores)
                logger.info(f"  📊 関連ページスコア分布: 最高={max_score:.4f}, 最低={min_score:.4f}, 平均={avg_score:.4f}")

            # 適応的閾値設定（上位10件程度を目標）
            page_threshold = 0.001  # 非常に低い閾値
            if page_scores:
                sorted_scores = sorted(page_scores, reverse=True)
                if len(sorted_scores) >= 10:
                    page_threshold = sorted_scores[9]  # 上位10件目のスコア
                    logger.info(f"  🔧 関連ページ適応的閾値: {page_threshold:.4f} (上位10件採用)")
                else:
                    page_threshold = min_score
                    logger.info(f"  🔧 関連ページ適応的閾値: {page_threshold:.4f} (全結果採用)")

            pages_filtered_count = 0
            for i, page in enumerate(web_detection.pages_with_matching_images):
                if page.url and page.url.startswith(('http://', 'https://')):
                    score = getattr(page, 'score', 0.0)
                    logger.info(f"  🔍 関連ページ候補 {i+1}: score={score:.4f}, url={page.url}")

                    if score >= page_threshold:
                        page_confidence = "高" if score >= 0.3 else "中" if score >= 0.1 else "低"
                        page_result = {
                            "url": page.url,
                            "search_method": "関連ページ",
                            "search_source": "Vision API",
                            "score": score,
                            "confidence": page_confidence
                        }
                        all_results.append(page_result)
                        logger.info(f"  ✅ 関連ページ追加 (score: {score:.4f}): {page.url}")
                    else:
                        pages_filtered_count += 1
                        logger.info(f"  ❌ 関連ページスコア不足 (score: {score:.4f}): {page.url}")

            logger.info(f"  📊 関連ページ結果: 採用={len(web_detection.pages_with_matching_images)-pages_filtered_count}件, 除外={pages_filtered_count}件")
        else:
            logger.info("💡 関連ページが0件でした")

        # 1-3. TEXT_DETECTION機能は削除（精度が低いため）
        logger.info(f"📝 テキスト検出機能はスキップ（精度向上のため無効化）")


        # 結果数制御（5-10件程度に調整）
        target_result_count = 8  # 目標結果数
        if len(all_results) > target_result_count:
            logger.info(f"🔧 結果数制御: {len(all_results)}件 -> {target_result_count}件に調整")

            # スコア順でソート
            all_results.sort(key=lambda x: x.get('score', 0.0), reverse=True)

            # 上位結果を選択（完全一致は必ず含める）
            filtered_results = []
            complete_matches = [r for r in all_results if r['search_method'] == '完全一致']
            other_results = [r for r in all_results if r['search_method'] != '完全一致']

            # 完全一致を全て追加
            filtered_results.extend(complete_matches)

            # 残り枠に他の結果を追加
            remaining_slots = target_result_count - len(complete_matches)
            if remaining_slots > 0:
                filtered_results.extend(other_results[:remaining_slots])

            all_results = filtered_results
            logger.info(f"  🎯 最終選択: 完全一致={len(complete_matches)}件, その他={len(filtered_results)-len(complete_matches)}件")

        # 最終統計（Vision API特化、類似画像除外）
        final_results_count = len(all_results)
        logger.info(f"✅ Vision API検出完了: {final_results_count}件のURL取得")
        logger.info(f"  - 完全一致: {len([r for r in all_results if r['search_method'] == '完全一致'])}件")
        logger.info(f"  - 部分一致: {len([r for r in all_results if r['search_method'] == '部分一致'])}件")
        logger.info(f"  - 関連ページ: {len([r for r in all_results if r['search_method'] == '関連ページ'])}件")

        # 重複除去のみ（信頼性・有効性チェックは削除）
        logger.info("🔧 URL重複除去開始...")
        logger.info(f"🔍 重複除去前の総URL数: {len(all_results)}件")

        filtered_results = []
        seen_urls = set()
        duplicate_count = 0

        for result in all_results:
            url = result["url"]

            if url in seen_urls:
                duplicate_count += 1
                continue
            seen_urls.add(url)

            # 全URLを取得URL一覧に含める（フィルタリングなし）
            filtered_results.append(result)
            logger.info(f"  ✅ URL追加 [{result['search_method']}]: {url}")

            # 最大100件に拡張（全て取得するため）
            if len(filtered_results) >= 100:
                break

        logger.info(f"🧹 重複除去統計: 重複除去={duplicate_count}件")
        logger.info(f"🌐 最終的に取得されたURL: {len(filtered_results)}件")

        # 検索方法別の統計
        method_stats = {}
        for result in filtered_results:
            method = result["search_method"]
            method_stats[method] = method_stats.get(method, 0) + 1

        logger.info(f"📊 検索方法別内訳:")
        for method, count in method_stats.items():
            logger.info(f"  - {method}: {count}件")

        # より詳細な統計
        logger.info(f"  - 全検索範囲合計: {len(filtered_results)}件")

        # 上位10件をログ出力
        for i, result in enumerate(filtered_results[:10]):
            logger.info(f"  {i+1}: [{result['search_method']}] {result['url']}")

        if len(filtered_results) > 10:
            logger.info(f"  ... 他 {len(filtered_results) - 10}件")

        return filtered_results

    except Exception as e:
        logger.error(f"❌ 画像検索エラー: {str(e)}")
        return []

def is_reliable_domain_relaxed(url: str) -> bool:
    """
    ドメイン信頼性チェック（最低限の除外のみ）
    本来の趣旨：怪しいドメインこそAI判定で悪用チェックするため、除外は最小限に
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # 最低限の除外：明らかに画像サービスのみ
        image_only_domains = [
            'i.imgur.com',
            'cdn.discordapp.com',
            'media.discordapp.net',
            'images.unsplash.com',
            'cdn.pixabay.com',
            'images.pexels.com',
        ]

        # 画像サービスのみ除外（他はすべてAI判定対象）
        for image_domain in image_only_domains:
            if image_domain in domain:
                logger.info(f"⏭️ 画像サービスのためスキップ: {domain}")
                return False

        # その他のドメインはすべて通す（悪用チェックのため）
        return True
    except Exception as e:
        logger.warning(f"⚠️ ドメイン信頼性チェック失敗 {url}: {e}")
        return True  # エラーの場合は通す

# search_with_serpapi関数を削除

def get_x_tweet_content(tweet_url: str) -> dict | None:
    """
    X（Twitter）のツイートURLから投稿内容とアカウント情報を取得
    X API v2のBearer Token認証を使用
    """
    if not X_BEARER_TOKEN:
        logger.warning("⚠️ X_BEARER_TOKEN が設定されていないため、ツイート内容取得をスキップ")
        return None

    try:
        import re
        from urllib.parse import urlparse

        # ツイートIDを抽出
        tweet_id_match = re.search(r'/status/(\d+)', tweet_url)
        if not tweet_id_match:
            logger.warning(f"⚠️ ツイートIDを抽出できません: {tweet_url}")
            return None

        tweet_id = tweet_id_match.group(1)
        logger.info(f"🐦 X API ツイート内容取得開始: ID={tweet_id}")

        # X API v2でツイート内容を取得（Bearer Token認証）
        headers = {
            'Authorization': f'Bearer {X_BEARER_TOKEN}',
            'Content-Type': 'application/json'
        }

        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                f"https://api.twitter.com/2/tweets/{tweet_id}",
                headers=headers,
                params={
                    'tweet.fields': 'text,author_id,created_at,public_metrics',
                    'user.fields': 'username,name,description,public_metrics',
                    'expansions': 'author_id'
                }
            )
            response.raise_for_status()

            data = response.json()

            if 'data' not in data:
                logger.warning(f"⚠️ ツイートデータが見つかりません: {tweet_id}")
                return None

            tweet_data = data['data']
            user_data = None

            # ユーザー情報を取得
            if 'includes' in data and 'users' in data['includes']:
                user_data = data['includes']['users'][0]

            # 結果を構造化
            result = {
                'tweet_id': tweet_id,
                'tweet_text': tweet_data.get('text', ''),
                'author_id': tweet_data.get('author_id', ''),
                'created_at': tweet_data.get('created_at', ''),
                'public_metrics': tweet_data.get('public_metrics', {}),
                'username': user_data.get('username', '') if user_data else '',
                'display_name': user_data.get('name', '') if user_data else '',
                'user_description': user_data.get('description', '') if user_data else '',
                'user_metrics': user_data.get('public_metrics', {}) if user_data else {}
            }

            logger.info(f"✅ X API取得成功: @{result['username']} - {result['tweet_text'][:50]}...")
            return result

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            logger.error("❌ X API認証エラー: Bearer Tokenが無効または期限切れです")
        elif e.response.status_code == 403:
            logger.error("❌ X API権限エラー: アクセス権限がありません")
        elif e.response.status_code == 404:
            logger.error("❌ ツイートが見つかりません（削除済みまたは非公開）")
        else:
            logger.error(f"❌ X API HTTPエラー: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logger.error(f"❌ X API一般エラー: {str(e)}")
        return None

def judge_x_content_with_gemini(x_data: dict) -> dict:
    """
    X（Twitter）の投稿内容とアカウント情報をGemini AIで判定
    """
    if not gemini_model:
        logger.warning("⚠️ Gemini モデルが初期化されていません")
        return {
            "judgment": "？",
            "reason": "Gemini AIが利用できません",
            "confidence": "不明"
        }

    try:
        # X投稿の詳細情報を構築
        username = x_data.get('username', '不明')
        display_name = x_data.get('display_name', '不明')
        tweet_text = x_data.get('tweet_text', '')
        user_description = x_data.get('user_description', '')

        # フォロワー数などの指標
        user_metrics = x_data.get('user_metrics', {})
        followers_count = user_metrics.get('followers_count', 0)
        following_count = user_metrics.get('following_count', 0)
        tweet_count = user_metrics.get('tweet_count', 0)

        # 投稿の指標
        public_metrics = x_data.get('public_metrics', {})
        retweet_count = public_metrics.get('retweet_count', 0)
        like_count = public_metrics.get('like_count', 0)
        reply_count = public_metrics.get('reply_count', 0)

        # Gemini用のプロンプトを構築（短縮版）
        prompt = f"""
【X投稿分析】
アカウント: @{username} ({display_name})
フォロワー: {followers_count:,}人
投稿内容: {tweet_text[:500]}

著作権侵害・違法コンテンツを判定してください。

判定基準：
○（安全）: 公式アカウント、正当な投稿
×（危険）: 著作権侵害、違法コンテンツ、海賊版
？（不明）: 判定困難

回答形式: "判定:[○/×/?] 理由:[150字以内の簡潔な理由]"
必ず150字以内で回答してください。
"""

        logger.info("🤖 Gemini AI X投稿判定開始")
        response = gemini_model.generate_content(prompt)

        if not response or not response.text:
            logger.warning("⚠️ Gemini AIからの応答が空です")
            return {
                "judgment": "？",
                "reason": "AI応答が空でした",
                "confidence": "不明"
            }

        response_text = response.text.strip()
        logger.info(f"📋 Gemini X投稿判定応答: {response_text}")

        # 応答を解析
        judgment = "？"
        reason = "判定できませんでした"

        if "判定:" in response_text and "理由:" in response_text:
            parts = response_text.split("理由:")
            judgment_part = parts[0].replace("判定:", "").strip()
            reason = parts[1].strip()

            if "○" in judgment_part:
                judgment = "○"
            elif "×" in judgment_part:
                judgment = "×"
            else:
                judgment = "？"
        else:
            # フォールバック解析
            if "○" in response_text:
                judgment = "○"
            elif "×" in response_text:
                judgment = "×"
            reason = response_text

        # 理由を300字以内に制限
        if len(reason) > 300:
            reason = reason[:297] + "..."
            logger.info(f"📝 X投稿判定理由を300字に短縮しました")

        # 信頼度を設定
        confidence = "高" if judgment in ["○", "×"] else "低"

        logger.info(f"✅ Gemini X投稿判定完了: {judgment} - {reason[:50]}...")

        return {
            "judgment": judgment,
            "reason": reason,
            "confidence": confidence,
            "x_data": x_data  # 元データも保持
        }

    except Exception as e:
        logger.error(f"❌ Gemini X投稿判定エラー: {str(e)}")
        return {
            "judgment": "？",
            "reason": f"判定エラー: {str(e)}",
            "confidence": "不明"
        }

def validate_url_availability_fast(url: str) -> bool:
    """
    URLの有効性を高速チェック（厳格版）
    白紙ページや無効なコンテンツを事前に除外
    Twitter画像URLは特別処理のため通す
    """
    try:
        # Twitter画像URLは特別処理のため、検証をスキップして通す
        if 'pbs.twimg.com' in url:
            logger.info(f"🐦 Twitter画像URL検出 - 特別処理のため通過: {url}")
            return True

        with httpx.Client(timeout=5.0, follow_redirects=True) as client:
            # 1. HEADリクエストでステータス確認
            try:
                head_response = client.head(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })

                # 4xx/5xxエラーは即座に除外
                if head_response.status_code >= 400:
                    logger.info(f"❌ HTTPエラー {head_response.status_code}: {url}")
                    return False

                # Content-Typeチェック
                content_type = head_response.headers.get('content-type', '').lower()
                if content_type and 'text/html' not in content_type:
                    logger.info(f"❌ 非HTMLコンテンツ ({content_type}): {url}")
                    return False

            except httpx.RequestError:
                # HEADが失敗した場合はGETで再試行
                pass

            # 2. GETリクエストでコンテンツの有効性を確認
            response = client.get(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })

            # ステータスコードチェック
            if not (200 <= response.status_code < 300):
                logger.info(f"❌ 無効ステータス {response.status_code}: {url}")
                return False

            # Content-Typeの最終確認
            content_type = response.headers.get('content-type', '').lower()
            if 'text/html' not in content_type:
                logger.info(f"❌ 非HTMLレスポンス ({content_type}): {url}")
                return False

            # コンテンツの実質性チェック
            content_length = len(response.text.strip())
            if content_length < 100:  # 100文字未満は空白ページとみなす
                logger.info(f"❌ 空白ページ (長さ: {content_length}): {url}")
                return False

            # 空白ページやエラーページの典型的なパターンをチェック
            content_lower = response.text.lower()
            error_indicators = [
                'page not found',
                'not found',
                '404',
                'error',
                'page does not exist',
                'página no encontrada',  # スペイン語の「ページが見つかりません」
                'no se encontró',
                'sin contenido',
                'empty page',
                'blank page'
            ]

            for indicator in error_indicators:
                if indicator in content_lower and content_length < 1000:
                    logger.info(f"❌ エラーページ検出 ('{indicator}'): {url}")
                    return False

            logger.info(f"✅ 有効なコンテンツを確認: {url}")
            return True

    except httpx.TimeoutException:
        logger.info(f"❌ タイムアウト: {url}")
        return False
    except httpx.RequestError as e:
        logger.info(f"❌ リクエストエラー: {url} - {e}")
        return False
    except Exception as e:
        logger.warning(f"⚠️ URL検証エラー: {url} - {e}")
        return False

def is_trusted_news_domain(url: str) -> bool:
    """
    信頼できるニュース・出版系ドメインかチェック
    これらのドメインはGemini判定をスキップして直接○判定
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

                # 信頼できるニュース・出版・公式サイトドメイン
        trusted_domains = [
            # 主要メディア・新聞
            'news.yahoo.co.jp', 'www.nhk.or.jp', 'nhk.or.jp', 'www3.nhk.or.jp',
            'mainichi.jp', 'www.mainichi.jp', 'www.asahi.com', 'asahi.com',
            'www.yomiuri.co.jp', 'yomiuri.co.jp', 'www.sankei.com', 'sankei.com',
            'www.nikkei.com', 'nikkei.com', 'www.jiji.com', 'jiji.com',
            'www.kyodo.co.jp', 'kyodo.co.jp', 'www.tokyo-np.co.jp', 'tokyo-np.co.jp',

            # 経済・ビジネス
            'toyokeizai.net', 'www.toyokeizai.net', 'diamond.jp', 'www.diamond.jp',
            'gendai.media', 'www.gendai.media', 'president.jp', 'www.president.jp',

            # 出版・メディア
            'bunshun.jp', 'www.bunshun.jp', 'shinchosha.co.jp', 'www.shinchosha.co.jp',
            'kadokawa.co.jp', 'www.kadokawa.co.jp', 'www.shogakukan.co.jp', 'shogakukan.co.jp',
            'www.shueisha.co.jp', 'shueisha.co.jp', 'www.kodansha.co.jp', 'kodansha.co.jp',

            # IT・テック
            'www.itmedia.co.jp', 'itmedia.co.jp', 'www.impress.co.jp', 'impress.co.jp',
            'ascii.jp', 'www.ascii.jp', 'internet.watch.impress.co.jp', 'gigazine.net',
            'www.gigazine.net', 'techcrunch.com', 'jp.techcrunch.com',

            # ゲーム・エンタメ
            'www.4gamer.net', '4gamer.net', 'www.famitsu.com', 'famitsu.com',
            'www.dengeki.com', 'dengeki.com', 'natalie.mu', 'www.natalie.mu',
            'comic-natalie.natalie.mu', 'music-natalie.natalie.mu', 'game-natalie.natalie.mu',
            'www.oricon.co.jp', 'oricon.co.jp', 'www.animeanime.jp', 'animeanime.jp',

            # 書店・EC
            'www.amazon.co.jp', 'amazon.co.jp', 'books.rakuten.co.jp', 'rakuten.co.jp',
            'honto.jp', 'www.honto.jp', 'www.kinokuniya.co.jp', 'kinokuniya.co.jp',
            'www.tsutaya.co.jp', 'tsutaya.co.jp', 'www.yodobashi.com', 'yodobashi.com',

            # ライフスタイル・ファッション
            'more.hpplus.jp', 'www.vogue.co.jp', 'vogue.co.jp', 'www.elle.com', 'elle.com',
            'www.cosmopolitan.com', 'cosmopolitan.com', 'mi-mollet.com', 'www.25ans.jp',
            'cancam.jp', 'www.cancam.jp', 'ray-web.jp', 'www.biteki.com', 'biteki.com'
        ]

        # 完全一致チェック
        if domain in trusted_domains:
            return True

        # サブドメインを含む部分一致チェック
        for trusted in trusted_domains:
            if domain.endswith('.' + trusted) or domain == trusted:
                return True

        # 楽天・Amazonの部分一致チェック（より広範囲に対応）
        trusted_patterns = [
            'rakuten.co.jp',  # search.rakuten.co.jp, books.rakuten.co.jp など
            'amazon.co.jp',  # www.amazon.co.jp など
            'amazon.com',  # www.amazon.com など
        ]

        for pattern in trusted_patterns:
            if pattern in domain:
                logger.info(f"✅ 信頼パターン一致: {pattern} in {domain}")
                return True

        return False
    except Exception as e:
        logger.warning(f"⚠️ ドメイン信頼性チェック失敗 {url}: {e}")
        return False

def convert_twitter_image_to_tweet_url(url: str) -> dict | None:
    """
    Twitter画像URL（pbs.twimg.com）から元ツイートのURLと内容を取得を試みる
    pbs.twimg.com画像URLからツイートIDを推定し、元のツイートURLを返す
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)

        # Twitter画像URLの場合
        if 'pbs.twimg.com' in parsed.netloc:
            logger.info(f"🐦 Twitter画像URL検出: {url}")

            # X APIまたはSerpAPIが利用可能な場合、ツイート検索を試行
            # if X_BEARER_TOKEN or (SERPAPI_KEY and SerpAPI_available): # SERPAPI_KEY をコメントアウト
            if X_BEARER_TOKEN:
                tweet_result = get_x_tweet_url_and_content_by_image(url)
                if tweet_result:
                    return tweet_result

                # 検索で見つからなかった場合でも、Geminiに画像の性質を伝える
                logger.info("🐦 ツイート内容は特定できませんでしたが、Twitter画像として処理")
                return {
                    "tweet_url": None,
                    "content": f"TWITTER_IMAGE_UNKNOWN:{url}"
                }

            # API利用不可の場合は従来の処理
            return {
                "tweet_url": None,
                "content": f"TWITTER_IMAGE:{url}"
            }

        return None
    except Exception as e:
        logger.warning(f"⚠️ Twitter URL変換失敗 {url}: {e}")
        return None

def get_x_tweet_url_and_content_by_image(image_url: str) -> dict | None:
    """
    画像URLからツイートURLと内容を探索する（高度版）
    Google Vision API + X API v2を組み合わせてツイートを特定
    戻り値: {"tweet_url": "https://x.com/...", "content": "ツイート内容"}
    """
    try:
        logger.info(f"🐦 画像URL経由でツイートURL検索: {image_url}")

        # 方法1: Google Vision APIのWEB_DETECTIONを使用
        if vision_client:
            try:
                logger.info("🔍 Google Vision APIでWEB_DETECTION実行中...")

                # 画像をダウンロード
                import httpx
                with httpx.Client(timeout=10.0) as client:
                    response = client.get(image_url)
                    if response.status_code == 200:
                        image_content = response.content

                        # Vision API実行
                        from google.cloud import vision
                        image = vision.Image(content=image_content)
                        response = vision_client.web_detection(image=image)  # type: ignore

                        # レスポンス確認
                        if not response or not response.web_detection:
                            logger.warning("⚠️ Vision APIレスポンスが無効")
                            return None

                        # 関連ページから X/Twitter URLを探索
                        if response.web_detection.pages_with_matching_images:
                            for page in response.web_detection.pages_with_matching_images[:15]:
                                if page.url and any(domain in page.url for domain in ['x.com', 'twitter.com']):
                                    logger.info(f"🐦 Vision APIでツイートURL発見: {page.url}")
                                    tweet_content = get_x_tweet_content(page.url)
                                    if tweet_content:
                                        return {
                                            "tweet_url": page.url,
                                            "content": tweet_content
                                        }

                        # より詳細な関連エンティティもチェック
                        if response.web_detection.web_entities:
                            for entity in response.web_detection.web_entities[:10]:
                                if entity.description:
                                    # エンティティの説明からTwitter関連キーワードを検索
                                    description = entity.description.lower()
                                    if any(keyword in description for keyword in ['twitter', 'tweet', 'x.com']):
                                        logger.info(f"🔍 関連エンティティ発見: {entity.description}")

                                        # このエンティティを使ってさらに検索（SerpAPI無効化）
                                        # if SERPAPI_KEY and SerpAPI_available:
                                        #     search = GoogleSearch({  # type: ignore
                                        #         "engine": "google",
                                        #         "q": f'site:x.com OR site:twitter.com "{entity.description}"',
                                        #         "api_key": SERPAPI_KEY,
                                        #         "num": 10
                                        #     })
                                        #     entity_results = search.get_dict()
                                        #     if "organic_results" in entity_results:
                                        #         for result in entity_results["organic_results"][:3]:
                                        #             if "link" in result and any(domain in result["link"] for domain in ['x.com', 'twitter.com']):
                                        #                 logger.info(f"🐦 エンティティ検索でツイートURL発見: {result['link']}")
                                        #                 tweet_content = get_x_tweet_content(result["link"])
                                        #                 if tweet_content:
                                        #                     return {
                                        #                         "tweet_url": result["link"],
                                        #                         "content": tweet_content
                                        #                     }
                                        logger.info("⚠️ SerpAPIエンティティ検索は無効化されています")

            except Exception as vision_error:
                logger.warning(f"⚠️ Vision API検索エラー: {vision_error}")

        # 方法2: 画像ファイル名からSnowflake IDを抽出してツイートIDを推定
        import re
        filename_match = re.search(r'/media/([^?]+)', image_url)
        if filename_match:
            filename = filename_match.group(1).split('.')[0]  # 拡張子を除去
            logger.info(f"🔍 画像ファイル名: {filename}")

            # Base64URLデコードを試行してSnowflake IDを取得
            try:
                import base64
                from datetime import datetime

                # Twitterの画像ファイル名は通常Base64URLエンコードされたSnowflake ID
                decoded_bytes = base64.urlsafe_b64decode(filename + '==')  # パディング追加
                snowflake_id = int.from_bytes(decoded_bytes, byteorder='big')

                # Snowflake IDからタイムスタンプを計算（Twitter Epoch: 2010-11-04 01:42:54 UTC）
                twitter_epoch = 1288834974657  # Twitter epoch in milliseconds
                timestamp_ms = (snowflake_id >> 22) + twitter_epoch
                tweet_datetime = datetime.fromtimestamp(timestamp_ms / 1000)

                logger.info(f"📅 推定投稿日時: {tweet_datetime}")

                # この情報を使ってより精密な検索を実行（SerpAPI無効化）
                # if SERPAPI_KEY and SerpAPI_available:
                #     date_str = tweet_datetime.strftime("%Y-%m-%d")
                #     search = GoogleSearch({  # type: ignore
                #         "engine": "google",
                #         "q": f'site:x.com OR site:twitter.com "{filename}" after:{date_str}',
                #         "api_key": SERPAPI_KEY,
                #         "num": 15
                #     })
                #
                #     date_results = search.get_dict()
                #     if "organic_results" in date_results:
                #         for result in date_results["organic_results"][:5]:
                #             if "link" in result and any(domain in result["link"] for domain in ['x.com', 'twitter.com']):
                #                 logger.info(f"🐦 日付検索でツイートURL発見: {result['link']}")
                #                 tweet_content = get_x_tweet_content(result["link"])


            except Exception as decode_error:
                logger.warning(f"⚠️ Snowflake ID デコード失敗: {decode_error}")

        logger.warning("⚠️ 画像からツイートURLを特定できませんでした")
        return None

    except Exception as e:
        logger.error(f"❌ 画像経由ツイートURL検索エラー: {str(e)}")
        return None

    try:
        if vision_client:
            try:
                logger.info("🔍 Google Vision APIでWEB_DETECTION実行中...")

                # 画像をダウンロード
                import httpx
                with httpx.Client(timeout=10.0) as client:
                    response = client.get(image_url)
                    if response.status_code == 200:
                        image_content = response.content

                        # Vision API実行
                        from google.cloud import vision
                        image = vision.Image(content=image_content)
                        response = vision_client.web_detection(image=image)  # type: ignore

                        # レスポンス確認
                        if not response or not response.web_detection:
                            logger.warning("⚠️ Vision APIレスポンスが無効")
                            return None

                        # 関連ページから X/Twitter URLを探索
                        if response.web_detection.pages_with_matching_images:
                            for page in response.web_detection.pages_with_matching_images[:15]:
                                if page.url and any(domain in page.url for domain in ['x.com', 'twitter.com']):
                                    logger.info(f"🐦 Vision APIでツイートURL発見: {page.url}")
                                    tweet_content = get_x_tweet_content(page.url)
                                    if tweet_content:
                                        return tweet_content

                        # より詳細な関連エンティティもチェック
                        if response.web_detection.web_entities:
                            for entity in response.web_detection.web_entities[:10]:
                                if entity.description:
                                    # エンティティの説明からTwitter関連キーワードを検索
                                    description = entity.description.lower()
                                    if any(keyword in description for keyword in ['twitter', 'tweet', 'x.com']):
                                        logger.info(f"🔍 関連エンティティ発見: {entity.description}")

                                        # このエンティティを使ってさらに検索（SerpAPI無効化）
                                        # if SERPAPI_KEY and SerpAPI_available:
                                        #     search = GoogleSearch({  # type: ignore
                                        #         "engine": "google",
                                        #         "q": f'site:x.com OR site:twitter.com "{entity.description}"',
                                        #         "api_key": SERPAPI_KEY,
                                        #         "num": 10
                                        #     })
                                        #     entity_results = search.get_dict()
                                        #     if "organic_results" in entity_results:
                                        #         for result in entity_results["organic_results"][:3]:
                                        #             if "link" in result and any(domain in result["link"] for domain in ['x.com', 'twitter.com']):
                                        #                 logger.info(f"🐦 エンティティ検索でツイートURL発見: {result['link']}")
                                        #                 tweet_content = get_x_tweet_content(result["link"])
                                        #                 if tweet_content:
                                        #                     return tweet_content
                                        logger.info("⚠️ SerpAPIエンティティ検索は無効化されています")

            except Exception as vision_error:
                logger.warning(f"⚠️ Vision API検索エラー: {vision_error}")

        # 方法2: 画像ファイル名からSnowflake IDを抽出してツイートIDを推定
        import re
        filename_match = re.search(r'/media/([^?]+)', image_url)
        if filename_match:
            filename = filename_match.group(1).split('.')[0]  # 拡張子を除去
            logger.info(f"🔍 画像ファイル名: {filename}")

            # Base64URLデコードを試行してSnowflake IDを取得
            try:
                import base64
                from datetime import datetime

                # Twitterの画像ファイル名は通常Base64URLエンコードされたSnowflake ID
                decoded_bytes = base64.urlsafe_b64decode(filename + '==')  # パディング追加
                snowflake_id = int.from_bytes(decoded_bytes, byteorder='big')

                # Snowflake IDからタイムスタンプを計算（Twitter Epoch: 2010-11-04 01:42:54 UTC）
                twitter_epoch = 1288834974657  # Twitter epoch in milliseconds
                timestamp_ms = (snowflake_id >> 22) + twitter_epoch
                tweet_datetime = datetime.fromtimestamp(timestamp_ms / 1000)

                logger.info(f"📅 推定投稿日時: {tweet_datetime}")

                # この情報を使ってより精密な検索を実行（SerpAPI無効化）
                # if SERPAPI_KEY and SerpAPI_available:
                #     date_str = tweet_datetime.strftime("%Y-%m-%d")
                #     search = GoogleSearch({  # type: ignore
                #         "engine": "google",
                #         "q": f'site:x.com OR site:twitter.com "{filename}" after:{date_str}',
                #         "api_key": SERPAPI_KEY,
                #         "num": 15
                #     })
                #
                #     date_results = search.get_dict()
                #     if "organic_results" in date_results:
                #         for result in date_results["organic_results"][:5]:
                #             if "link" in result and any(domain in result["link"] for domain in ['x.com', 'twitter.com']):
                #                 logger.info(f"🐦 日付検索でツイートURL発見: {result['link']}")
                #                 tweet_content = get_x_tweet_content(result["link"])
                #                 if tweet_content:
                #                     return tweet_content


            except Exception as decode_error:
                logger.warning(f"⚠️ Snowflake ID デコード失敗: {decode_error}")

        logger.warning("⚠️ 画像からツイート内容を特定できませんでした")
        return None

    except Exception as e:
        logger.error(f"❌ 画像経由ツイート検索エラー: {str(e)}")
        return None

        logger.info(f"📝 スクレイピング完了: {len(content)} chars")
        return content

    except httpx.HTTPStatusError as e:
        logger.error(f"❌ HTTPステータスエラー {url}: {e.response.status_code} {e.response.reason_phrase}")
        return None
    except Exception as e:
        logger.error(f"❌ スクレイピング一般エラー {url}: {e}")
        return None



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
        if not validate_file(file):
            allowed_types = ["image/jpeg", "image/png", "image/jpg", "image/gif", "image/webp"]
            if PDF_SUPPORT:
                allowed_types.append("application/pdf")

            logger.error(f"❌ 無効なファイル形式: {file.content_type}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_file_format",
                    "message": "無効なファイル形式です。JPEG、PNG、GIF、WebP、PDF形式のファイルをアップロードしてください。" if PDF_SUPPORT else "無効なファイル形式です。JPEG、PNG、GIF、WebP形式の画像をアップロードしてください。",
                    "allowed_types": allowed_types,
                    "received_type": file.content_type
                }
            )

        logger.info("✅ ファイル形式検証OK")

        # ファイル読み込み（サイズ制限なし）
        content = await file.read()
        file_size_mb = len(content) / (1024 * 1024)
        logger.info(f"📊 ファイルサイズ: {file_size_mb:.2f}MB")

        # ファイル種別による検証
        is_pdf = is_pdf_file(file.content_type or "", file.filename or "")

        if is_pdf:
            # PDF検証
            if not PDF_SUPPORT:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "pdf_not_supported",
                        "message": "PDF処理ライブラリがインストールされていません。",
                        "install_instruction": "pip install PyMuPDF"
                    }
                )

            try:
                # PDFの有効性を確認
                test_images = convert_pdf_to_images(content)
                if not test_images:
                    raise Exception("PDFから画像を抽出できませんでした")
                logger.info(f"✅ PDF有効性検証OK ({len(test_images)}ページ)")
            except Exception as e:
                logger.error(f"❌ PDF検証失敗: {str(e)}")
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "corrupted_pdf",
                        "message": "破損したPDFファイルです。有効なPDFをアップロードしてください。",
                        "validation_error": str(e)
                    }
                )
        else:
            # 画像検証
            try:
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
            "status": "uploaded",
            "file_type": "pdf" if is_pdf else "image"
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
    # Vision API接続テスト
    vision_api_status = "not_configured"
    vision_api_error = None

    if vision_client:
        try:
            # 小さなテスト画像でVision APIをテスト
            from PIL import Image as PILImage
            import io

            # 1x1の小さな白い画像を作成
            test_image = PILImage.new('RGB', (1, 1), color='white')
            img_buffer = io.BytesIO()
            test_image.save(img_buffer, format='PNG')
            test_image_content = img_buffer.getvalue()

            # Vision APIテスト呼び出し
            image = vision.Image(content=test_image_content)
            response = vision_client.web_detection(image=image)  # type: ignore

            if hasattr(response, 'error') and response.error:
                error_code = getattr(response.error, 'code', 'UNKNOWN')
                error_message = getattr(response.error, 'message', '詳細不明')

                # エラーコードが0（OK）以外の場合のみエラーとして処理
                if error_code != 0:
                    vision_api_status = "error"
                    vision_api_error = f"Code: {error_code}, Message: {error_message}"
                else:
                    vision_api_status = "healthy"
                    vision_api_error = None
            else:
                vision_api_status = "healthy"

        except Exception as e:
            vision_api_status = "error"
            vision_api_error = str(e)

    return {
        "status": "healthy" if vision_api_status in ["healthy", "not_configured"] else "degraded",
        "api_keys": {
            "gemini_api_key_configured": GEMINI_API_KEY is not None,
            "google_vision_api_configured": GOOGLE_APPLICATION_CREDENTIALS is not None,
            "vision_api_client_initialized": vision_client is not None,
            "vision_api_status": vision_api_status,
            "vision_api_error": vision_api_error
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
    """指定された画像IDに対してWeb検索を実行し、関連画像のURLリストを取得する"""

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
    file_path = record["file_path"]
    file_type = record.get("file_type", "image")

    logger.info(f"📁 検索対象ファイル: {file_path} (type: {file_type})")

    try:
        # ファイルを開いてコンテンツを読み込む
        with open(file_path, 'rb') as file:
            file_content = file.read()

        logger.info(f"📸 ファイル読み込み完了: {len(file_content)} bytes")

        # ファイル種別に応じて処理を分岐
        if file_type == "pdf":
            # PDFの場合：各ページを画像に変換して処理
            logger.info("📄 PDF処理開始...")

            pdf_images = convert_pdf_to_images(file_content)
            if not pdf_images:
                raise Exception("PDFから画像を抽出できませんでした")

            logger.info(f"📄 PDF処理完了: {len(pdf_images)}ページを抽出")

            # 各ページの画像ハッシュを計算（最初のページをメインハッシュとする）
            image_hash = calculate_image_hash(pdf_images[0])
            logger.info(f"🔑 画像ハッシュ計算完了（ページ1）: {image_hash[:16]}...")

            # 各ページを個別に分析（拡張検索）
            all_url_lists = []
            for i, page_image_content in enumerate(pdf_images):
                logger.info(f"🌐 ページ {i+1} の拡張画像検索実行中（逆検索機能付き）...")
                page_urls = enhanced_image_search_with_reverse(page_image_content)
                all_url_lists.extend(page_urls)
                logger.info(f"✅ ページ {i+1} 拡張Web検索完了: {len(page_urls)}件のURLを発見")

            # 重複URLを除去（辞書形式データ対応）
            seen_urls = set()
            url_list = []
            for url_data in all_url_lists:
                url = url_data["url"] if isinstance(url_data, dict) else url_data
                if url not in seen_urls:
                    seen_urls.add(url)
                    url_list.append(url_data)
            logger.info(f"📋 全ページ統合結果: {len(url_list)}件の一意なURLを発見")

        else:
            # 画像の場合：従来の処理
            image_content = file_content

            # 画像ハッシュを計算
            image_hash = calculate_image_hash(image_content)
            logger.info(f"🔑 画像ハッシュ計算完了: {image_hash[:16]}...")

            # 拡張画像検索（逆検索機能付き）
            logger.info("🌐 拡張画像検索実行中（逆検索機能付き）...")
            url_list = enhanced_image_search_with_reverse(image_content)
            logger.info(f"✅ 拡張Web検索完了: {len(url_list)}件のURLを発見")

        # 各URLを効率的に分析（ニュースサイトは事前○判定、Twitterは特別処理）
        processed_results = []

        for i, url_data in enumerate(url_list[:50]):  # PDFの場合は最大50件に拡張
            # url_dataが辞書形式の場合とstring形式の場合に対応
            if isinstance(url_data, dict):
                url = url_data["url"]
                search_method = url_data.get("search_method", "不明")
                search_source = url_data.get("search_source", "不明")
                confidence = url_data.get("confidence", "不明")
            else:
                # 後方互換性のため、string形式もサポート
                url = url_data
                search_method = "不明"
                search_source = "不明"
                confidence = "不明"

            logger.info(f"🔄 URL処理中 ({i+1}/{min(len(url_list), 50)}): [{search_method}] {url}")

            # 効率的な分析実行
            result = analyze_url_efficiently(url)

            if result:
                # 検索方法の情報を結果に追加
                result["search_method"] = search_method
                result["search_source"] = search_source
                result["confidence"] = confidence
                processed_results.append(result)
                logger.info(f"  ✅ 処理完了: {result['judgment']} - {result['reason']}")
            else:
                # 分析失敗時
                processed_results.append({
                    "url": url,
                    "judgment": "？",
                    "reason": "分析に失敗しました",
                    "search_method": search_method,
                    "search_source": search_source,
                    "confidence": confidence
                })
                logger.info(f"  ❌ 分析失敗: {url}")

        # 最終結果を保存（生の検索結果も含める）
        search_results[image_id] = {
            "processed_results": processed_results,
            "raw_urls": url_list,  # 生の検索結果（search_method, search_source, confidence付き）
            "total_found": len(url_list),
            "total_processed": len(processed_results)
        }

        # アップロード記録を更新
        record["analysis_status"] = "completed"
        record["analysis_time"] = datetime.now().isoformat()
        record["found_urls_count"] = len(url_list)
        record["processed_results_count"] = len(processed_results)
        record["image_hash"] = image_hash
        save_records()

        # 履歴に保存
        save_analysis_to_history(image_id, image_hash, processed_results)

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

    # 検索結果データを取得
    search_data = search_results.get(image_id, {})

    # 新旧データ構造に対応
    if isinstance(search_data, list):
        # 旧データ構造（後方互換性）
        processed_results = search_data
        raw_urls = []
        total_found = len(search_data)
        total_processed = len(search_data)
    else:
        # 新データ構造
        processed_results = search_data.get("processed_results", [])
        raw_urls = search_data.get("raw_urls", [])
        total_found = search_data.get("total_found", 0)
        total_processed = search_data.get("total_processed", 0)

    # 正常な結果を返す
    return {
        "success": True,
        "image_id": image_id,
        "analysis_status": "completed",
        "original_filename": record.get("original_filename", "不明"),
        "analysis_time": record.get("analysis_time", "不明"),
        "found_urls_count": record.get("found_urls_count", total_found),
        "processed_results_count": record.get("processed_results_count", total_processed),
        "results": processed_results,
        "raw_urls": raw_urls,  # 生の検索結果を追加
        "search_summary": {
            "total_found": total_found,
            "total_processed": total_processed,
            "search_methods": generate_search_method_summary(raw_urls)
        }
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
    search_results[test_image_id] = {
        "processed_results": dummy_results,
        "raw_urls": [],
        "total_found": len(dummy_results),
        "total_processed": len(dummy_results)
    }

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

def generate_evidence_hash(data: dict) -> str:
    """
    証拠データのハッシュ値を生成（改ざん防止用）
    """
    # データを文字列として正規化してハッシュ化
    json_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(json_str.encode('utf-8')).hexdigest()

def create_evidence_data(image_id: str) -> dict:
    """
    証拠データを作成する
    """
    if image_id not in upload_records:
        raise HTTPException(
            status_code=404,
            detail="指定されたimage_idが見つかりません。"
        )

    if image_id not in search_results:
        raise HTTPException(
            status_code=404,
            detail="この画像の分析結果が見つかりません。先に分析を実行してください。"
        )

    upload_record = upload_records[image_id]
    analysis_results = search_results[image_id]

    # 現在時刻
    current_time = datetime.now()

    # 証拠データを構築
    evidence_data = {
        "evidence_info": {
            "creation_date": current_time.isoformat(),
            "creation_timestamp": int(current_time.timestamp()),
            "evidence_id": f"evidence_{image_id}_{int(current_time.timestamp())}",
            "system_info": "Book Leak Detector v1.0.0"
        },
        "image_info": {
            "image_id": image_id,
            "original_filename": upload_record.get("original_filename", "不明"),
            "file_size": upload_record.get("file_size", 0),
            "upload_time": upload_record.get("upload_time", "不明"),
            "content_type": upload_record.get("content_type", "不明")
        },
        "analysis_info": {
            "analysis_time": upload_record.get("analysis_time", "不明"),
            "analysis_status": upload_record.get("analysis_status", "不明"),
            "found_urls_count": upload_record.get("found_urls_count", 0),
            "processed_results_count": upload_record.get("processed_results_count", 0)
        },
        "detection_results": {
            "total_urls_detected": len(analysis_results),
            "url_analysis": []
        }
    }

    # 各URLの判定結果を追加
    for result in analysis_results:
        url_info = {
            "url": result.get("url", ""),
            "judgment": result.get("judgment", "？"),
            "reason": result.get("reason", "理由不明"),
            "analysis_timestamp": current_time.isoformat()
        }
        evidence_data["detection_results"]["url_analysis"].append(url_info)

    # ハッシュ値を計算（改ざん防止用）
    evidence_data["integrity"] = {
        "hash_algorithm": "SHA-256",
        "data_hash": generate_evidence_hash(evidence_data),
        "note": "このハッシュ値は証拠データの改ざんを検知するために使用されます"
    }

    return evidence_data

@app.get("/api/evidence/download/{image_id}")
async def download_evidence(image_id: str):
    """
    検出結果を証拠として保存用JSONファイルをダウンロードする
    """
    logger.info(f"📥 証拠保全要求: image_id={image_id}")

    try:
        # 証拠データを作成
        evidence_data = create_evidence_data(image_id)

        # JSONファイル名を生成
        timestamp = int(datetime.now().timestamp())
        filename = f"evidence_{image_id}_{timestamp}.json"

        # JSONデータを文字列に変換
        json_content = json.dumps(evidence_data, ensure_ascii=False, indent=2)

        logger.info(f"✅ 証拠保全データ生成完了: {filename}")

        # ファイルとしてレスポンスを返す
        return Response(
            content=json_content,
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "application/json; charset=utf-8"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 証拠保全エラー: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "evidence_creation_failed",
                "message": f"証拠データの生成に失敗しました: {str(e)}",
                "image_id": image_id
            }
        )

@app.get("/api/history")
async def get_analysis_history():
    """
    過去の検査履歴一覧を取得する
    """
    logger.info(f"📚 履歴取得要求: {len(analysis_history)}件")

    try:
        # 履歴を新しい順にソート
        sorted_history = sorted(
            analysis_history,
            key=lambda x: x.get("analysis_timestamp", 0),
            reverse=True
        )

        # 表示用に履歴データを整形
        formatted_history = []
        for entry in sorted_history:
            formatted_entry = {
                "history_id": entry.get("history_id"),
                "image_id": entry.get("image_id"),
                "image_hash": entry.get("image_hash"),
                "original_filename": entry.get("original_filename"),
                "analysis_date": entry.get("analysis_date"),
                "analysis_timestamp": entry.get("analysis_timestamp"),
                "found_urls_count": entry.get("found_urls_count", 0),
                "processed_results_count": entry.get("processed_results_count", 0),
                "summary": {
                    "safe_count": len([r for r in entry.get("results", []) if r.get("judgment") == "○"]),
                    "suspicious_count": len([r for r in entry.get("results", []) if r.get("judgment") == "×"]),
                    "unknown_count": len([r for r in entry.get("results", []) if r.get("judgment") in ["？", "！"]])
                }
            }
            formatted_history.append(formatted_entry)

        return {
            "success": True,
            "total_history_count": len(analysis_history),
            "history": formatted_history
        }

    except Exception as e:
        logger.error(f"❌ 履歴取得エラー: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "history_retrieval_failed",
                "message": f"履歴の取得に失敗しました: {str(e)}"
            }
        )

@app.delete("/api/history/{history_id}")
async def delete_analysis_history(history_id: str):
    """
    指定された履歴IDの検査履歴を削除する
    """
    try:
        # 指定されたhistory_idの履歴を検索
        history_to_delete = None
        for i, entry in enumerate(analysis_history):
            if entry.get("history_id") == history_id:
                history_to_delete = analysis_history.pop(i)
                break

        if not history_to_delete:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "history_not_found",
                    "message": "指定された履歴が見つかりません"
                }
            )

        # 履歴ファイルを更新
        save_history()

        logger.info(f"🗑️ 履歴削除完了: {history_id}")

        return {
            "success": True,
            "message": "履歴を削除しました",
            "deleted_history_id": history_id,
            "deleted_filename": history_to_delete.get("original_filename", "不明")
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 履歴削除エラー: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "history_deletion_failed",
                "message": f"履歴の削除に失敗しました: {str(e)}"
            }
        )

@app.get("/api/history/details/{history_id}")
async def get_history_details(history_id: str):
    """
    指定された履歴IDの詳細（検出されたURLと判定結果）を取得する
    """
    try:
        # 指定されたhistory_idの履歴を検索
        target_history = None
        for entry in analysis_history:
            if entry.get("history_id") == history_id:
                target_history = entry
                break

        if not target_history:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "history_not_found",
                    "message": "指定された履歴が見つかりません"
                }
            )

        # 詳細情報を整形
        results = target_history.get("results", [])

        return {
            "success": True,
            "history_id": history_id,
            "image_id": target_history.get("image_id"),
            "original_filename": target_history.get("original_filename"),
            "analysis_date": target_history.get("analysis_date"),
            "found_urls_count": target_history.get("found_urls_count", 0),
            "processed_results_count": target_history.get("processed_results_count", 0),
            "results": results,
            "summary": {
                "safe_count": len([r for r in results if r.get("judgment") == "○"]),
                "suspicious_count": len([r for r in results if r.get("judgment") == "×"]),
                "warning_count": len([r for r in results if r.get("judgment") == "！"]),
                "unknown_count": len([r for r in results if r.get("judgment") == "？"])
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 履歴詳細取得エラー: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "history_details_retrieval_failed",
                "message": f"履歴詳細の取得に失敗しました: {str(e)}"
            }
        )

@app.get("/api/history/diff/{image_id}")
async def get_analysis_diff(image_id: str):
    """
    指定された画像IDの前回検査との差分を取得する
    """
    logger.info(f"🔄 差分取得要求: image_id={image_id}")

    try:
        # 現在の結果を取得
        if image_id not in upload_records:
            raise HTTPException(
                status_code=404,
                detail="指定されたimage_idが見つかりません。"
            )

        record = upload_records[image_id]
        search_data = search_results.get(image_id, {})
        current_results = search_data.get("processed_results", []) if isinstance(search_data, dict) else []
        image_hash = record.get("image_hash")

        if not image_hash:
            return {
                "success": True,
                "has_previous": False,
                "message": "この画像に対する過去の分析結果がありません。"
            }

        # 同じハッシュの過去の分析結果を取得
        previous_analysis = get_previous_analysis(image_hash)

        if not previous_analysis:
            return {
                "success": True,
                "has_previous": False,
                "message": "この画像に対する過去の分析結果がありません。"
            }

        # 差分を計算
        diff_result = calculate_diff(current_results, previous_analysis.get("results", []))

        # 前回分析日時を含めて返す
        response_data = {
            "success": True,
            "has_previous": True,
            "image_id": image_id,
            "image_hash": image_hash,
            "current_analysis": {
                "analysis_date": record.get("analysis_time"),
                "results_count": len(current_results)
            },
            "previous_analysis": {
                "analysis_date": previous_analysis.get("analysis_date"),
                "results_count": len(previous_analysis.get("results", []))
            },
            "diff": diff_result
        }

        logger.info(f"✅ 差分計算完了: 新規={diff_result['total_new']}, 消失={diff_result['total_disappeared']}, 変更={diff_result['total_changed']}")

        return response_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 差分取得エラー: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "diff_calculation_failed",
                "message": f"差分の計算に失敗しました: {str(e)}",
                "image_id": image_id
            }
        )

def generate_csv_report(image_id: str) -> str:
    """
    CSV形式のレポートを生成する
    """
    if image_id not in upload_records:
        raise HTTPException(
            status_code=404,
            detail="指定されたimage_idが見つかりません。"
        )

    record = upload_records[image_id]
    results = search_results.get(image_id, [])

    # StringIOを使ってCSVデータを生成
    output = StringIO()

    # BOM付きUTF-8のためのBOMを追加
    output.write('\ufeff')

    writer = csv.writer(output)

    # ヘッダー行（日本語）
    headers = [
        "検査日時",
        "画像ファイル名",
        "URL",
        "ドメイン",
        "判定結果",
        "判定理由"
    ]
    writer.writerow(headers)

    # データ行
    analysis_time = record.get("analysis_time", "不明")
    filename = record.get("original_filename", "不明")

    for result in results:
        url = result.get("url", "")
        judgment = result.get("judgment", "？")
        reason = result.get("reason", "理由不明")

        # ドメインを抽出
        try:
            domain = urlparse(url).netloc
        except:
            domain = "不明"

        writer.writerow([
            analysis_time,
            filename,
            url,
            domain,
            judgment,
            reason
        ])

    return output.getvalue()

def generate_summary_report(image_id: str) -> dict:
    """
    経営層向けサマリーレポートを生成する
    """
    if image_id not in upload_records:
        raise HTTPException(
            status_code=404,
            detail="指定されたimage_idが見つかりません。"
        )

    record = upload_records[image_id]
    results = search_results.get(image_id, [])

    # 統計を計算
    total_count = len(results)
    safe_count = len([r for r in results if r.get("judgment") == "○"])
    dangerous_count = len([r for r in results if r.get("judgment") == "×"])
    warning_count = len([r for r in results if r.get("judgment") in ["？", "！"]])

    # 危険なドメインを集計
    dangerous_domains = {}
    for result in results:
        if result.get("judgment") == "×":
            try:
                domain = urlparse(result.get("url", "")).netloc
                if domain:
                    dangerous_domains[domain] = dangerous_domains.get(domain, 0) + 1
            except:
                pass

    # TOP5危険ドメイン
    top_dangerous = sorted(dangerous_domains.items(), key=lambda x: x[1], reverse=True)[:5]

    # 推奨アクション
    if dangerous_count > 0:
        if dangerous_count >= 3:
            recommended_action = "至急対応が必要"
            action_details = f"{dangerous_count}件の危険サイトが検出されました。法的対応を検討してください。"
        else:
            recommended_action = "要注意・監視継続"
            action_details = f"{dangerous_count}件の危険サイトが検出されました。継続的な監視が必要です。"
    elif warning_count > 0:
        recommended_action = "経過観察"
        action_details = f"{warning_count}件の不明サイトが検出されました。定期的な再検査を推奨します。"
    else:
        recommended_action = "安全"
        action_details = "危険なサイトは検出されませんでした。"

    return {
        "summary": {
            "analysis_date": record.get("analysis_time", "不明"),
            "image_filename": record.get("original_filename", "不明"),
            "total_detected": total_count,
            "safe_sites": safe_count,
            "dangerous_sites": dangerous_count,
            "warning_sites": warning_count
        },
        "risk_assessment": {
            "level": "高" if dangerous_count >= 3 else "中" if dangerous_count > 0 else "低",
            "recommended_action": recommended_action,
            "action_details": action_details
        },
        "top_dangerous_domains": [
            {"domain": domain, "count": count} for domain, count in top_dangerous
        ],
        "recommendations": [
            "定期的な再検査の実施",
            "検出された危険サイトへの法的対応",
            "社内への注意喚起と教育",
            "検出結果の社内共有"
        ]
    }

@app.get("/api/report/csv/{image_id}")
async def download_csv_report(image_id: str):
    """
    CSV形式のレポートをダウンロードする
    """
    logger.info(f"📊 CSVレポート生成要求: image_id={image_id}")

    try:
        # CSVデータを生成
        csv_content = generate_csv_report(image_id)

        # ファイル名を生成
        timestamp = int(datetime.now().timestamp())
        filename = f"leak_detection_report_{image_id}_{timestamp}.csv"

        logger.info(f"✅ CSVレポート生成完了: {filename}")

        # CSVファイルとしてレスポンスを返す
        return Response(
            content=csv_content.encode('utf-8'),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "text/csv; charset=utf-8"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ CSVレポート生成エラー: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "csv_report_generation_failed",
                "message": f"CSVレポートの生成に失敗しました: {str(e)}",
                "image_id": image_id
            }
        )

@app.get("/api/report/summary/{image_id}")
async def get_summary_report(image_id: str):
    """
    経営層向けサマリーレポートを取得する
    """
    logger.info(f"📊 サマリーレポート生成要求: image_id={image_id}")

    try:
        # サマリーレポートを生成
        summary_data = generate_summary_report(image_id)

        logger.info(f"✅ サマリーレポート生成完了: {image_id}")

        return {
            "success": True,
            "image_id": image_id,
            "report": summary_data,
            "generated_at": datetime.now().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ サマリーレポート生成エラー: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "summary_report_generation_failed",
                "message": f"サマリーレポートの生成に失敗しました: {str(e)}",
                "image_id": image_id
            }
        )

@app.post("/batch-upload")
async def batch_upload_images(files: List[UploadFile] = File(...)):
    """
    複数の画像を一括でアップロードする
    """
    logger.info(f"📤 バッチアップロード開始: {len(files)}ファイル")

    # ファイル数制限チェック
    if len(files) > 10:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "too_many_files",
                "message": "ファイル数が上限を超えています。最大10ファイルまでです。",
                "max_files": 10,
                "received_files": len(files)
            }
        )

    total_size = 0
    uploaded_files = []
    errors = []

    for i, file in enumerate(files):
        try:
            logger.info(f"📁 ファイル処理中 ({i+1}/{len(files)}): {file.filename}")

            # ファイル検証
            if not validate_file(file):
                errors.append({
                    "filename": file.filename,
                    "error": "invalid_file_format",
                    "message": f"無効なファイル形式: {file.content_type}"
                })
                continue

            # ファイル読み込み
            content = await file.read()
            file_size = len(content)
            total_size += file_size

            # 合計サイズ制限チェック（50MB）
            if total_size > 50 * 1024 * 1024:
                errors.append({
                    "filename": file.filename,
                    "error": "total_size_exceeded",
                    "message": "合計ファイルサイズが50MBを超えています"
                })
                break

            # ファイルサイズ情報をログ出力（制限は行わない）
            logger.info(f"📊 {file.filename}: {file_size / (1024*1024):.1f}MB")

            # ファイル種別による検証
            is_pdf = is_pdf_file(file.content_type or "", file.filename or "")

            if is_pdf:
                # PDF検証
                if not PDF_SUPPORT:
                    errors.append({
                        "filename": file.filename,
                        "error": "pdf_not_supported",
                        "message": "PDF処理ライブラリがインストールされていません"
                    })
                    continue

                try:
                    # PDFの有効性を確認
                    test_images = convert_pdf_to_images(content)
                    if not test_images:
                        raise Exception("PDFから画像を抽出できませんでした")
                except Exception as e:
                    errors.append({
                        "filename": file.filename,
                        "error": "corrupted_pdf",
                        "message": f"破損したPDFファイル: {str(e)}"
                    })
                    continue
            else:
                # 画像検証
                try:
                    image = Image.open(BytesIO(content))
                    image.verify()
                except Exception as e:
                    errors.append({
                        "filename": file.filename,
                        "error": "corrupted_image",
                        "message": f"破損した画像ファイル: {str(e)}"
                    })
                    continue

            # ファイル保存
            file_id = str(uuid.uuid4())
            file_extension = os.path.splitext(file.filename or "image")[1].lower() or ".jpg"
            safe_filename = f"{file_id}{file_extension}"
            file_path = os.path.join(UPLOAD_DIR, safe_filename)

            with open(file_path, "wb") as f:
                f.write(content)

            # 記録保存
            upload_record = {
                "id": file_id,
                "original_filename": file.filename,
                "saved_filename": safe_filename,
                "file_path": file_path,
                "content_type": file.content_type,
                "file_size": file_size,
                "upload_time": datetime.now().isoformat(),
                "status": "uploaded",
                "batch_upload": True,
                "file_type": "pdf" if is_pdf else "image"
            }

            upload_records[file_id] = upload_record
            uploaded_files.append({
                "file_id": file_id,
                "filename": file.filename,
                "size": file_size,
                "status": "success"
            })

            logger.info(f"✅ ファイル保存完了: {file.filename} -> {file_id}")

        except Exception as e:
            logger.error(f"❌ ファイル処理エラー {file.filename}: {str(e)}")
            errors.append({
                "filename": file.filename,
                "error": "processing_failed",
                "message": str(e)
            })

    # 記録を保存
    save_records()

    logger.info(f"✅ バッチアップロード完了: 成功={len(uploaded_files)}件, エラー={len(errors)}件")

    return {
        "success": True,
        "total_files": len(files),
        "uploaded_count": len(uploaded_files),
        "error_count": len(errors),
        "total_size": total_size,
        "files": uploaded_files,
        "errors": errors,
        "upload_time": datetime.now().isoformat()
    }

@app.post("/batch-search")
async def batch_search_images(
    background_tasks: BackgroundTasks,
    request: dict,
    batch_id: Optional[str] = None
):
    """
    複数の画像を一括で検索する
    """
    # リクエストボディから file_ids を取得
    file_ids = request.get("file_ids", [])
    if not file_ids:
        raise HTTPException(
            status_code=422,
            detail="file_ids is required in request body"
        )

    if not batch_id:
        batch_id = str(uuid.uuid4())

    logger.info(f"🔍 バッチ検索開始: batch_id={batch_id}, {len(file_ids)}ファイル")

    # バッチジョブ初期化
    batch_jobs[batch_id] = {
        "batch_id": batch_id,
        "total_files": len(file_ids),
        "completed_files": 0,
        "status": "processing",
        "start_time": datetime.now().isoformat(),
        "files": []
    }

    # 各ファイルの初期状態を設定（すべてのfile_idsに対応）
    for file_id in file_ids:
        if file_id in upload_records:
            batch_jobs[batch_id]["files"].append({
                "file_id": file_id,
                "filename": upload_records[file_id].get("original_filename", "不明"),
                "status": "pending",
                "progress": 0
            })
        else:
            # 存在しないファイルも配列に追加（エラー状態で）
            batch_jobs[batch_id]["files"].append({
                "file_id": file_id,
                "filename": "ファイルが見つかりません",
                "status": "error",
                "progress": 0,
                "error": "アップロードレコードが見つかりません"
            })

    # バックグラウンドで処理開始
    background_tasks.add_task(lambda: process_batch_search(batch_id, file_ids))

    return {
        "success": True,
        "batch_id": batch_id,
        "message": f"バッチ検索を開始しました。{len(file_ids)}ファイルを処理します。",
        "total_files": len(file_ids)
    }

def process_batch_search(batch_id: str, file_ids: List[str]):
    """
    バッチ検索をバックグラウンドで実行
    """
    try:
        for i, file_id in enumerate(file_ids):
            if batch_id not in batch_jobs:
                return

            # 既にエラー状態のファイルをスキップ
            if batch_jobs[batch_id]["files"][i]["status"] == "error":
                logger.info(f"⏭️ スキップ ({i+1}/{len(file_ids)}): {file_id} - 既にエラー状態")
                batch_jobs[batch_id]["completed_files"] = i + 1
                continue

            # ファイル状態を更新
            batch_jobs[batch_id]["files"][i]["status"] = "processing"
            batch_jobs[batch_id]["files"][i]["progress"] = 0

            logger.info(f"🔄 バッチ検索処理中 ({i+1}/{len(file_ids)}): {file_id}")
            logger.info(f"📊 バッチ進捗: {i+1}/{len(file_ids)} ({((i+1)/len(file_ids)*100):.1f}%)")

            try:
                # 既存の分析ロジックを使用
                if file_id not in upload_records:
                    batch_jobs[batch_id]["files"][i]["status"] = "error"
                    batch_jobs[batch_id]["files"][i]["error"] = "ファイルが見つかりません"
                    continue

                record = upload_records[file_id]
                file_path = record["file_path"]
                file_type = record.get("file_type", "image")

                # ファイル読み込み
                with open(file_path, 'rb') as file:
                    file_content = file.read()

                # プログレス更新
                batch_jobs[batch_id]["files"][i]["progress"] = 10

                # ファイル種別に応じて処理を分岐
                if file_type == "pdf":
                    # PDFの場合：各ページを画像に変換して処理
                    pdf_images = convert_pdf_to_images(file_content)
                    if not pdf_images:
                        raise Exception("PDFから画像を抽出できませんでした")

                    # 各ページの画像ハッシュを計算（最初のページをメインハッシュとする）
                    image_hash = calculate_image_hash(pdf_images[0])

                    # プログレス更新
                    batch_jobs[batch_id]["files"][i]["progress"] = 25

                    # 各ページを個別に分析（拡張検索）
                    all_url_lists = []
                    for page_i, page_image_content in enumerate(pdf_images):
                        page_urls = enhanced_image_search_with_reverse(page_image_content)
                        all_url_lists.extend(page_urls)

                        # ページごとのプログレス更新
                        page_progress = 25 + (page_i + 1) * 35 // len(pdf_images)
                        batch_jobs[batch_id]["files"][i]["progress"] = min(page_progress, 60)

                    # 重複URLを除去（辞書形式データ対応）
                    seen_urls = set()
                    url_list = []
                    for url_data in all_url_lists:
                        url = url_data["url"] if isinstance(url_data, dict) else url_data
                        if url not in seen_urls:
                            seen_urls.add(url)
                            url_list.append(url_data)

                else:
                    # 画像の場合：従来の処理
                    image_content = file_content
                    image_hash = calculate_image_hash(image_content)

                    # プログレス更新
                    batch_jobs[batch_id]["files"][i]["progress"] = 20

                    # 拡張Web検索実行（逆検索機能付き）
                    url_list = enhanced_image_search_with_reverse(image_content)

                # プログレス更新
                batch_jobs[batch_id]["files"][i]["progress"] = 60

                # URL分析
                processed_results = []
                for j, url_data in enumerate(url_list[:50]):
                    # url_dataが辞書形式の場合とstring形式の場合に対応
                    if isinstance(url_data, dict):
                        url = url_data["url"]
                        search_method = url_data.get("search_method", "不明")
                        search_source = url_data.get("search_source", "不明")
                        confidence = url_data.get("confidence", "不明")
                    else:
                        # 後方互換性のため、string形式もサポート
                        url = url_data
                        search_method = "不明"
                        search_source = "不明"
                        confidence = "不明"

                    result = analyze_url_efficiently(url)
                    if result:
                        # 検索方法の情報を結果に追加
                        result["search_method"] = search_method
                        result["search_source"] = search_source
                        result["confidence"] = confidence
                        processed_results.append(result)

                    # 小刻みな進捗更新
                    progress = 60 + (j + 1) * 30 // min(len(url_list), 50)  # 60% + 30%分を URL分析で使用
                    batch_jobs[batch_id]["files"][i]["progress"] = min(progress, 90)

                # 結果保存（生の検索結果も含める）
                search_results[file_id] = {
                    "processed_results": processed_results,
                    "raw_urls": url_list,  # 生の検索結果（search_method, search_source, confidence付き）
                    "total_found": len(url_list),
                    "total_processed": len(processed_results)
                }

                # アップロード記録更新
                record["analysis_status"] = "completed"
                record["analysis_time"] = datetime.now().isoformat()
                record["found_urls_count"] = len(url_list)
                record["processed_results_count"] = len(processed_results)
                record["image_hash"] = image_hash

                # 履歴保存
                save_analysis_to_history(file_id, image_hash, processed_results)

                # 完了状態更新
                batch_jobs[batch_id]["files"][i]["status"] = "completed"
                batch_jobs[batch_id]["files"][i]["progress"] = 100
                batch_jobs[batch_id]["files"][i]["results_count"] = len(processed_results)

                logger.info(f"✅ バッチ検索完了 ({i+1}/{len(file_ids)}): {file_id}")
                logger.info(f"📊 ファイル {i+1} の結果: URL発見={len(url_list)}件, 分析完了={len(processed_results)}件")

            except Exception as e:
                logger.error(f"❌ バッチ検索エラー {file_id}: {str(e)}")
                batch_jobs[batch_id]["files"][i]["status"] = "error"
                batch_jobs[batch_id]["files"][i]["error"] = str(e)

            # 完了ファイル数更新
            batch_jobs[batch_id]["completed_files"] = i + 1

            # メモリ最適化（各ファイル処理後）
            gc.collect()

        # 全体完了
        batch_jobs[batch_id]["status"] = "completed"
        batch_jobs[batch_id]["end_time"] = datetime.now().isoformat()
        save_records()

        logger.info(f"✅ バッチ検索全体完了: batch_id={batch_id}")

    except Exception as e:
        import traceback
        logger.error(f"❌ バッチ検索全体エラー: {str(e)}")
        logger.error(f"❌ エラー詳細: {traceback.format_exc()}")
        if batch_id in batch_jobs:
            batch_jobs[batch_id]["status"] = "error"
            batch_jobs[batch_id]["error"] = str(e)
            batch_jobs[batch_id]["end_time"] = datetime.now().isoformat()

@app.get("/batch-status/{batch_id}")
async def get_batch_status(batch_id: str):
    """
    バッチ処理の進捗状況を取得
    """
    if batch_id not in batch_jobs:
        raise HTTPException(
            status_code=404,
            detail="指定されたバッチIDが見つかりません。"
        )

    return {
        "success": True,
        "batch": batch_jobs[batch_id]
    }

@app.get("/image/{file_id}")
async def get_image(file_id: str):
    """
    アップロードされた画像ファイルを取得（エラー回避強化版）
    """
    try:
        if file_id not in upload_records:
            logger.warning(f"⚠️ 画像取得: 存在しないfile_id {file_id}")
            raise HTTPException(
                status_code=404,
                detail="指定された画像が見つかりません"
            )

        record = upload_records[file_id]
        file_path = record.get("file_path")

        if not file_path:
            logger.warning(f"⚠️ 画像取得: file_pathが空 {file_id}")
            raise HTTPException(
                status_code=404,
                detail="ファイルパスが記録されていません"
            )

        if not os.path.exists(file_path):
            logger.warning(f"⚠️ ファイル消失検出: {file_id} - {file_path}")

            # PDFファイルの場合は代替処理を提供
            if record.get("file_type") == "pdf":
                raise HTTPException(
                    status_code=404,
                    detail=f"PDFファイルが見つかりません（再デプロイにより消失）: {record.get('original_filename', 'unknown')}"
                )
            else:
                raise HTTPException(
                    status_code=404,
                        detail=f"ファイルが見つかりません（再デプロイにより消失）: {record.get('original_filename', 'unknown')}"
                )

        # ファイル拡張子から適切なメディアタイプを判定
        _, ext = os.path.splitext(file_path)
        media_type_map = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.pdf': 'application/pdf'
        }
        media_type = media_type_map.get(ext.lower(), 'image/jpeg')

        return FileResponse(
            file_path,
            media_type=media_type,
            filename=record.get("original_filename", f"image{ext}")
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 画像取得エラー {file_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="画像ファイルの取得中にエラーが発生しました"
        )

@app.get("/file-info/{file_id}")
async def get_file_info(file_id: str):
    """
    ファイルの情報（ファイル名、タイプ等）を取得する
    """
    if file_id not in upload_records:
        raise HTTPException(
            status_code=404,
            detail="指定されたファイルが見つかりません"
        )

    record = upload_records[file_id]

    # ファイルの物理的存在をチェック
    file_path = record.get("file_path", "")
    file_exists = os.path.exists(file_path) if file_path else False

    return {
        "file_id": file_id,
        "filename": record.get("original_filename", "不明"),
        "fileType": record.get("file_type", "image"),
        "fileSize": record.get("file_size", 0),
        "uploadTime": record.get("upload_time", ""),
        "analysisStatus": record.get("analysis_status", "pending"),
        "fileExists": file_exists,
        "filePath": file_path if file_exists else None
    }

@app.get("/pdf-preview/{file_id}")
async def get_pdf_preview(file_id: str):
    """
    PDFファイルの最初のページを画像として取得する（エラー回避強化版）
    """
    try:
        if file_id not in upload_records:
            logger.warning(f"⚠️ PDFプレビュー: 存在しないfile_id {file_id}")
            raise HTTPException(
                status_code=404,
                detail="指定されたファイルが見つかりません"
            )

        record = upload_records[file_id]
        file_path = record.get("file_path")

        if not file_path:
            logger.warning(f"⚠️ PDFプレビュー: file_pathが空 {file_id}")
            raise HTTPException(
                status_code=404,
                detail="ファイルパスが記録されていません"
            )

        if not os.path.exists(file_path):
            logger.warning(f"⚠️ PDFプレビュー: ファイル消失検出 {file_id} - {file_path}")
            raise HTTPException(
                status_code=404,
                detail=f"PDFファイルが見つかりません（再デプロイにより消失）: {record.get('original_filename', 'unknown')}"
            )

        # PDFファイルかチェック
        if record.get("file_type") != "pdf":
            logger.warning(f"⚠️ PDFプレビュー: PDF以外のファイル {file_id}")
            raise HTTPException(
                status_code=400,
                detail="指定されたファイルはPDFではありません"
            )

        # PDFの最初のページを画像に変換
        with open(file_path, 'rb') as file:
            pdf_content = file.read()

        pdf_images = convert_pdf_to_images(pdf_content)
        if not pdf_images:
            logger.error(f"❌ PDFプレビュー: 画像変換失敗 {file_id}")
            raise HTTPException(
                status_code=500,
                detail="PDFから画像を生成できませんでした"
            )

        # 最初のページの画像を返す
        first_page_image = pdf_images[0]

        return Response(
            content=first_page_image,
            media_type="image/png",
            headers={"Content-Disposition": f"inline; filename=\"{file_id}_preview.png\""}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ PDFプレビュー生成エラー {file_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"PDFプレビューの生成中にエラーが発生しました: {str(e)}"
        )

# URL分析関数群
def analyze_url_efficiently(url: str) -> dict | None:
    """
    URLを効率的に分析し、判定結果を返す
    X URLは特別処理でAPI経由で詳細分析
    """
    try:
        logger.info(f"🔄 URL分析開始: {url}")

        # X (Twitter) URLの特別処理
        if 'twitter.com' in url or 'x.com' in url:
            logger.info(f"🐦 X URL検出 - API経由で詳細分析: {url}")

            # X APIでツイート内容を取得
            x_data = get_x_tweet_content(url)
            if x_data:
                # Gemini AIで判定
                judgment_result = judge_x_content_with_gemini(x_data)

                # 結果を構築
                return {
                    "url": url,
                    "judgment": judgment_result["judgment"],
                    "reason": judgment_result["reason"],
                    "confidence": judgment_result["confidence"],
                    "analysis_type": "X API + Gemini AI",
                    "x_username": x_data.get("username", ""),
                    "x_display_name": x_data.get("display_name", ""),
                    "x_tweet_text": x_data.get("tweet_text", "")[:100] + "..." if len(x_data.get("tweet_text", "")) > 100 else x_data.get("tweet_text", "")
                }
            else:
                # X API取得失敗時はスクレイピングにフォールバック
                logger.warning(f"⚠️ X API取得失敗、スクレイピングにフォールバック: {url}")
                return analyze_url_with_scraping(url)

        # その他のURLは通常のスクレイピング分析
        else:
            return analyze_url_with_scraping(url)

    except Exception as e:
        logger.error(f"❌ URL分析エラー {url}: {str(e)}")
        return None

def analyze_url_with_scraping(url: str) -> dict | None:
    """
    URLをドメイン分類に基づいて効率的に判定
    公式ドメイン → 即時○判定（Gemini API不使用）
    非公式/SNS → Gemini AIで詳細分析
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # 1. 公式・信頼ドメインの即時○判定（Gemini API不使用）
        official_domains = [
            # 大手EC・公式サイト
            'amazon.co.jp', 'amazon.com', 'rakuten.co.jp', 'yahoo.co.jp',
            'mercari.com', 'mercari.jp', 'paypay.ne.jp', 'paypaymall.yahoo.co.jp',

            # 大手企業公式
            'nintendo.com', 'sony.com', 'microsoft.com', 'apple.com',
            'google.com', 'youtube.com', 'wikipedia.org',

            # 政府・教育機関
            'gov.jp', 'go.jp', 'ac.jp', 'ed.jp',

            # 大手メディア・ニュース
            'nhk.or.jp', 'asahi.com', 'yomiuri.co.jp', 'mainichi.jp',
            'nikkei.com', 'sankei.com', 'tokyo-np.co.jp',

            # エンタメ・専門メディア
            'famitsu.com', 'oricon.co.jp', 'natalie.mu',
            'animenewsnetwork.com', 'seigura.com', 'dengekionline.com',

            # 出版社公式
            'kadokawa.co.jp', 'shogakukan.co.jp', 'kodansha.co.jp',
            'shueisha.co.jp', 'hakusensha.co.jp', 'futabasha.co.jp',

            # ゲーム・アニメ公式
            'square-enix.com', 'bandai.co.jp', 'konami.com',
            'capcom.com', 'sega.com', 'atlus.com'
        ]

        for official in official_domains:
            if official in domain:
                logger.info(f"✅ 公式ドメインのため即時○判定（Gemini API不使用）: {url}")
                return {
                    "url": url,
                    "judgment": "○",
                    "reason": "信頼できる公式サイト",
                    "confidence": "高",
                    "analysis_type": "公式ドメイン即時判定",
                    "domain_category": "公式サイト"
                }

        # 2. 非公式・SNS・不明ドメインの詳細分析（Gemini API使用）
        logger.info(f"🔍 非公式ドメイン検出 - Gemini AIで詳細分析: {url}")

        # ドメインカテゴリを判定
        domain_category = classify_domain_type(domain)

        # スクレイピングしてコンテンツ取得
        content = scrape_page_content(url)
        if not content:
            return {
                "url": url,
                "judgment": "？",
                "reason": "ページ内容を取得できませんでした",
                "confidence": "不明",
                "analysis_type": "スクレイピング失敗",
                "domain_category": domain_category
            }

        # Gemini AIで詳細判定
        judgment_result = judge_content_with_gemini(content, domain_category)

        return {
            "url": url,
            "judgment": judgment_result["judgment"],
            "reason": judgment_result["reason"],
            "confidence": judgment_result["confidence"],
            "analysis_type": "Gemini AI詳細分析",
            "domain_category": domain_category
        }

    except Exception as e:
        logger.error(f"❌ URL分析エラー {url}: {str(e)}")
        return None

def classify_domain_type(domain: str) -> str:
    """
    ドメインのタイプを分類
    """
    domain_lower = domain.lower()

    # SNS・ソーシャルメディア
    if any(sns in domain_lower for sns in [
        'twitter.com', 'x.com', 'instagram.com', 'facebook.com',
        'tiktok.com', 'youtube.com', 'pinterest.com', 'tumblr.com',
        'threads.net', 'discord.com', 'reddit.com'
    ]):
        return "SNS・ソーシャルメディア"

    # ブログ・個人サイト
    elif any(blog in domain_lower for blog in [
        'blog', 'diary', 'note.', 'hatenablog', 'ameblo', 'fc2',
        'wordpress', 'blogspot', 'medium.com'
    ]):
        return "ブログ・個人サイト"

    # ファイル共有・アップロードサイト
    elif any(file_share in domain_lower for file_share in [
        'mediafire', 'mega.nz', 'dropbox', 'drive.google',
        'onedrive', 'box.com', 'wetransfer'
    ]):
        return "ファイル共有サイト"

    # 掲示板・フォーラム
    elif any(forum in domain_lower for forum in [
        '2ch', '5ch', 'reddit', 'discord', 'slack'
    ]):
        return "掲示板・フォーラム"

    # その他・不明
    else:
        return "その他・不明サイト"

def judge_content_with_gemini(content: str, domain_category: str = "不明") -> dict:
    """
    ページコンテンツをGemini AIで判定
    """
    if not gemini_model:
        return {
            "judgment": "？",
            "reason": "Gemini AIが利用できません",
            "confidence": "不明"
        }

    try:
        prompt = f"""
【ドメイン分類】{domain_category}
【ページ内容】{content[:1500]}

著作権侵害・違法コンテンツを判定してください。

判定基準：
○（安全）: 公式サイト、正当なコンテンツ
×（危険）: 著作権侵害、違法コンテンツ、海賊版
？（不明）: 判定困難

回答形式: "判定:[○/×/?] 理由:[150字以内の簡潔な理由]"
必ず150字以内で回答してください。
"""

        logger.info("🤖 Gemini AI判定開始")
        response = gemini_model.generate_content(prompt)

        if not response or not response.text:
            return {
                "judgment": "？",
                "reason": "AI応答が空でした",
                "confidence": "不明"
            }

        response_text = response.text.strip()
        logger.info(f"📋 Gemini応答: {response_text}")

        # 応答を解析
        judgment = "？"
        reason = "判定できませんでした"

        if "判定:" in response_text and "理由:" in response_text:
            parts = response_text.split("理由:")
            judgment_part = parts[0].replace("判定:", "").strip()
            reason = parts[1].strip()

            if "○" in judgment_part:
                judgment = "○"
            elif "×" in judgment_part:
                judgment = "×"
            else:
                judgment = "？"
        else:
            # フォールバック解析
            if "○" in response_text:
                judgment = "○"
            elif "×" in response_text:
                judgment = "×"
            reason = response_text

        # 理由を300字以内に制限
        if len(reason) > 300:
            reason = reason[:297] + "..."
            logger.info(f"📝 理由を300字に短縮しました")

        logger.info(f"✅ Gemini判定完了: {judgment} - {reason[:50]}...")

        return {
            "judgment": judgment,
            "reason": reason,
            "confidence": "高" if judgment in ["○", "×"] else "低"
        }

    except Exception as e:
        logger.error(f"❌ Gemini判定エラー: {str(e)}")
        return {
            "judgment": "？",
            "reason": f"判定エラー: {str(e)}",
            "confidence": "不明"
        }

def scrape_page_content(url: str) -> str | None:
    """
    URLからページ内容をスクレイピング
    """
    # 画像URLの場合はドメインベースで分類
    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
    if any(url.lower().endswith(ext) for ext in image_extensions):
        logger.info(f"🖼️ 画像URL検出 - ドメインベース分類: {url}")
        return f"画像URL: {url}"

    # Instagram専用処理
    if 'instagram.com' in url:
        return extract_instagram_content(url)

    # Threads専用処理
    if 'threads.net' in url:
        return extract_threads_content(url)

    logger.info(f"🌐 スクレイピング開始: {url}")
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            # Content-Typeを事前確認
            try:
                head_response = client.head(url, headers={'User-Agent': 'Mozilla/5.0'})
                content_type = head_response.headers.get('content-type', '').lower()
                if 'text/html' not in content_type:
                    logger.info(f"⏭️  HTMLでないためスキップ (Content-Type: {content_type}): {url}")
                    return None
            except httpx.RequestError as e:
                logger.warning(f"⚠️ HEADリクエスト失敗 (GETで続行): {e}")

            # GETリクエストでコンテンツ取得
            response = client.get(url, headers={'User-Agent': 'Mozilla/5.0'})
            response.raise_for_status()

        # BeautifulSoupで解析
        soup = BeautifulSoup(response.text, 'html.parser')
        title = soup.title.string if soup.title else ""
        body_text = " ".join([p.get_text() for p in soup.find_all('p', limit=5)])

        content = f"Title: {title.strip()}\n\nBody: {body_text.strip()}"
        logger.info(f"📝 スクレイピング完了: {len(content)} chars")
        return content

    except httpx.HTTPStatusError as e:
        logger.error(f"❌ HTTPステータスエラー {url}: {e.response.status_code} {e.response.reason_phrase}")
        return None
    except Exception as e:
        logger.error(f"❌ スクレイピング一般エラー {url}: {e}")
        return None

def extract_instagram_content(url: str) -> str:
    """Instagram投稿から内容を抽出"""
    try:
        logger.info(f"📸 Instagram専用解析: {url}")

        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            response = client.get(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # メタデータから情報を抽出
        title = ""
        description = ""

        # og:title
        og_title = soup.find('meta', property='og:title')
        if og_title:
            title = og_title.get('content', '')

        # og:description
        og_desc = soup.find('meta', property='og:description')
        if og_desc:
            description = og_desc.get('content', '')

        content = f"Instagram投稿\nタイトル: {title}\n説明: {description}"
        logger.info(f"📸 Instagram解析完了: {len(content)} chars")
        return content

    except Exception as e:
        return f"Instagram投稿: {url}"

def extract_threads_content(url: str) -> str:
    """Threads投稿から内容を抽出"""
    try:
        logger.info(f"🧵 Threads専用解析: {url}")

        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            response = client.get(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # メタデータから情報を抽出
        title = ""
        description = ""

        # og:title
        og_title = soup.find('meta', property='og:title')
        if og_title:
            title = og_title.get('content', '')

        # og:description
        og_desc = soup.find('meta', property='og:description')
        if og_desc:
            description = og_desc.get('content', '')

        content = f"Threads投稿\nタイトル: {title}\n説明: {description}"
        logger.info(f"🧵 Threads解析完了: {len(content)} chars")
        return content

    except Exception as e:
        return f"Threads投稿: {url}"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)