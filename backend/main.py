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
import hashlib
import csv
from io import StringIO
from urllib.parse import urlparse
from fastapi.responses import Response
try:
    from serpapi import GoogleSearch  # type: ignore
except ImportError:
    GoogleSearch = None
    print("⚠️ SerpAPI not available - continuing without it")

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
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
X_API_KEY = os.getenv("X_API_KEY")
X_API_SECRET = os.getenv("X_API_SECRET")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET")
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")

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
if not SERPAPI_KEY:
    missing_keys.append("SERPAPI_KEY (精度向上用)")
if not X_BEARER_TOKEN:
    missing_keys.append("X_BEARER_TOKEN (Twitter内容取得用)")

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
    print("- SERPAPI_KEY: SerpAPI用（精度向上）")
    print("- X_BEARER_TOKEN: X API用（Twitter内容取得）")
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
HISTORY_FILE = "history.json"

# メモリ内履歴データストレージ
analysis_history: List[Dict] = []

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

def search_web_for_image(image_content: bytes) -> list[str]:
    """
    画像コンテンツを受け取り、Google Vision API + SerpAPIで
    類似・同一画像が使用されているURLのリストを返す。
    精度向上のため、両方のAPIを組み合わせて使用。
    """
    logger.info("🔍 画像検索開始（Vision API + SerpAPI併用）")

    all_urls = []

    try:
        # 1. Google Vision API WEB_DETECTION
        logger.info("📊 【Phase 1】Google Vision API WEB_DETECTION")
        image = vision.Image(content=image_content)
        response = vision_client.web_detection(image=image)  # type: ignore
        web_detection = response.web_detection

        # デバッグ用: 各マッチタイプの件数をログ出力
        exact_matches_count = len(web_detection.best_guess_labels) if web_detection.best_guess_labels else 0
        full_matching_count = len(web_detection.full_matching_images) if web_detection.full_matching_images else 0
        partial_matching_count = len(web_detection.partial_matching_images) if web_detection.partial_matching_images else 0
        pages_count = len(web_detection.pages_with_matching_images) if web_detection.pages_with_matching_images else 0

        logger.info(f"📈 Vision API検出結果:")
        logger.info(f"  - 完全一致ページ数: {exact_matches_count}件")
        logger.info(f"  - 完全一致画像数: {full_matching_count}件")
        logger.info(f"  - 部分一致画像数: {partial_matching_count}件（高品質のみ使用）")
        logger.info(f"  - マッチ画像含むページ数: {pages_count}件")

        vision_urls = []

        # Vision APIからURL収集
        if web_detection.pages_with_matching_images:
            logger.info("🎯 マッチページからURL抽出中...")
            for page in web_detection.pages_with_matching_images:
                if page.url and page.url.startswith(('http://', 'https://')):
                    score = getattr(page, 'score', 1.0)
                    if score >= 0.1 or score == 0.0:
                        vision_urls.append(page.url)
                        logger.info(f"  ✅ ページ追加 (score: {score:.2f}): {page.url}")

        if web_detection.full_matching_images:
            logger.info("🎯 完全一致画像からURL抽出中...")
            for img in web_detection.full_matching_images:
                if img.url and img.url.startswith(('http://', 'https://')):
                    vision_urls.append(img.url)
                    logger.info(f"  ✅ 完全一致画像追加: {img.url}")

        if web_detection.partial_matching_images and len(vision_urls) < 5:
            logger.info("🎯 高品質部分一致からURL補完中...")
            for i, img in enumerate(web_detection.partial_matching_images[:5]):
                if img.url and img.url.startswith(('http://', 'https://')):
                    vision_urls.append(img.url)
                    logger.info(f"  ✅ 部分一致補完追加: {img.url}")

        all_urls.extend(vision_urls)
        logger.info(f"✅ Vision API: {len(vision_urls)}件のURL取得")

        # 2. SerpAPI 画像逆検索（追加検索）
        logger.info("📊 【Phase 2】SerpAPI 画像逆検索")

        # Vision APIで取得した画像URLを使ってSerpAPI検索
        serpapi_urls = []
        if vision_urls and SERPAPI_KEY:
            # 最初の数個の画像URLでSerpAPI検索を実行
            for i, img_url in enumerate(vision_urls[:3]):  # 最初の3つで検索
                logger.info(f"🔍 SerpAPI検索 ({i+1}/3): {img_url}")
                serp_results = search_with_serpapi(img_url)
                serpapi_urls.extend(serp_results)

                if len(serpapi_urls) >= 10:  # 十分な数が集まったら停止
                    break

        all_urls.extend(serpapi_urls)
        logger.info(f"✅ SerpAPI: {len(serpapi_urls)}件のURL取得")

        # 重複除去とフィルタリング
        logger.info("🔧 URL品質フィルタリング開始...")
        filtered_urls = []
        seen = set()

        for url in all_urls:
            if url in seen:
                continue
            seen.add(url)

            # ドメイン信頼性チェック（最低限の除外のみ）
            if not is_reliable_domain_relaxed(url):
                continue

            # URL有効性チェック（厳格版）
            logger.info(f"🔍 URL有効性チェック中: {url}")
            if not validate_url_availability_fast(url):
                logger.info(f"  ❌ 無効URLスキップ: {url}")
                continue

            filtered_urls.append(url)
            logger.info(f"  ✅ 有効URL追加: {url}")

            # 最大25件に制限（両API併用により増加）
            if len(filtered_urls) >= 25:
                break

        logger.info(f"🌐 最終的に選別されたURL: {len(filtered_urls)}件")
        logger.info(f"📊 内訳: Vision API={len(vision_urls)}件, SerpAPI={len(serpapi_urls)}件")

        for i, url in enumerate(filtered_urls[:10]):
            logger.info(f"  {i+1}: {url}")

        if len(filtered_urls) > 10:
            logger.info(f"  ... 他 {len(filtered_urls) - 10}件")

        return filtered_urls

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

def search_with_serpapi(image_url: str) -> list[str]:
    """
    SerpAPIを使用して画像の逆検索を実行
    Google Vision APIと組み合わせて精度向上
    """
    if not SERPAPI_KEY:
        logger.warning("⚠️ SERPAPI_KEY が設定されていないため、SerpAPI検索をスキップ")
        return []

    if not GoogleSearch:
        logger.warning("⚠️ SerpAPIライブラリが利用できないため、SerpAPI検索をスキップ")
        return []

    logger.info("🔍 SerpAPI画像逆検索開始")

    try:
        # SerpAPIで画像検索を実行
        search = GoogleSearch({
            "engine": "google_images",
            "q": image_url,
            "api_key": SERPAPI_KEY,
            "tbs": "simg",  # 類似画像検索
            "num": 20,      # 最大20件取得
            "safe": "off"   # セーフサーチ無効
        })

        results = search.get_dict()

        if "images_results" not in results:
            logger.warning("⚠️ SerpAPI: 画像検索結果が見つかりません")
            return []

        urls = []
        for result in results["images_results"][:15]:  # 上位15件
            if "link" in result:
                urls.append(result["link"])
            elif "original" in result:
                urls.append(result["original"])

        logger.info(f"✅ SerpAPI検索完了: {len(urls)}件のURLを発見")
        return urls

    except Exception as e:
        logger.error(f"❌ SerpAPI検索エラー: {str(e)}")
        return []

def get_x_tweet_content(tweet_url: str) -> str | None:
    """
    X（Twitter）のツイートURLから投稿内容を取得
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
        logger.info(f"🐦 ツイート内容取得開始: ID={tweet_id}")

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
                    'user.fields': 'username,name',
                    'expansions': 'author_id'
                }
            )

            if response.status_code == 200:
                data = response.json()
                if 'data' in data:
                    tweet_text = data['data'].get('text', '')
                    author_info = ""

                    # 作者情報も取得
                    if 'includes' in data and 'users' in data['includes']:
                        user = data['includes']['users'][0]
                        username = user.get('username', '')
                        name = user.get('name', '')
                        author_info = f"@{username} ({name})"

                    logger.info(f"✅ ツイート内容取得完了: {len(tweet_text)}文字")
                    return f"X投稿内容 {author_info}: {tweet_text}"
                else:
                    logger.warning("⚠️ ツイートデータが見つかりません")
                    return None
            else:
                logger.error(f"❌ X API エラー: {response.status_code} - {response.text}")
                return None

    except Exception as e:
        logger.error(f"❌ X API取得エラー: {str(e)}")
        return None

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
            'news.yahoo.co.jp',
            'www3.nhk.or.jp',
            'mainichi.jp',
            'www.asahi.com',
            'www.yomiuri.co.jp',
            'www.sankei.com',
            'www.nikkei.com',
            'toyokeizai.net',
            'diamond.jp',
            'gendai.media',
            'bunshun.jp',
            'shinchosha.co.jp',
            'kadokawa.co.jp',
            'www.shogakukan.co.jp',
            'www.amazon.co.jp',
            'books.rakuten.co.jp',
            'honto.jp',
            'www.kinokuniya.co.jp',
            'www.tsutaya.co.jp',
            'natalie.mu',
            'www.oricon.co.jp',
            'more.hpplus.jp',
            'www.vogue.co.jp',
            'www.elle.com',
            'www.cosmopolitan.com',
            'mi-mollet.com',
            'www.25ans.jp',
            'cancam.jp',
            'ray-web.jp',
            'www.biteki.com'
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
            'amazon.co.jp',   # www.amazon.co.jp など
            'amazon.com',     # www.amazon.com など
        ]

        for pattern in trusted_patterns:
            if pattern in domain:
                logger.info(f"✅ 信頼パターン一致: {pattern} in {domain}")
                return True

        return False
    except Exception as e:
        logger.warning(f"⚠️ ドメイン信頼性チェック失敗 {url}: {e}")
        return False

def convert_twitter_image_to_tweet(url: str) -> str | None:
    """
    Twitter画像URL（pbs.twimg.com）から元ツイートの内容を取得を試みる
    pbs.twimg.com画像URLからツイートIDを推定し、X APIで内容取得
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)

        # Twitter画像URLの場合
        if 'pbs.twimg.com' in parsed.netloc:
            logger.info(f"🐦 Twitter画像URL検出: {url}")

            # X APIまたはSerpAPIが利用可能な場合、ツイート検索を試行
            if X_BEARER_TOKEN or (SERPAPI_KEY and GoogleSearch):
                tweet_content = get_x_tweet_content_by_image(url)
                if tweet_content:
                    return tweet_content

                # 検索で見つからなかった場合でも、Geminiに画像の性質を伝える
                logger.info("🐦 ツイート内容は特定できませんでしたが、Twitter画像として処理")
                return f"TWITTER_IMAGE_UNKNOWN:{url}"

            # API利用不可の場合は従来の処理
            return f"TWITTER_IMAGE:{url}"

        return None
    except Exception as e:
        logger.warning(f"⚠️ Twitter URL変換失敗 {url}: {e}")
        return None

def get_x_tweet_content_by_image(image_url: str) -> str | None:
    """
    画像URLからツイート内容を探索する
    X API v2とSerpAPIを組み合わせてツイートを特定
    """
    try:
        logger.info(f"🐦 画像URL経由でツイート検索: {image_url}")

        # 方法1: SerpAPIでTwitter内検索
        if SERPAPI_KEY and GoogleSearch:
            logger.info("🔍 SerpAPIでTwitter検索実行中...")
            search = GoogleSearch({
                "engine": "google",
                "q": f"site:twitter.com {image_url}",
                "api_key": SERPAPI_KEY,
                "num": 10
            })

            results = search.get_dict()

            if "organic_results" in results:
                for result in results["organic_results"][:5]:
                    if "link" in result and "twitter.com" in result["link"]:
                        logger.info(f"🐦 ツイートURL発見: {result['link']}")
                        # ツイートURLが見つかった場合、内容を取得
                        tweet_content = get_x_tweet_content(result["link"])
                        if tweet_content:
                            return tweet_content

        # 方法2: 画像URLからツイートIDを推定（部分的）
        if X_BEARER_TOKEN:
            logger.info("🔍 X APIで直接検索試行中...")
            # pbs.twimg.com URLから可能な限りメタデータを抽出
            # 実際のツイートIDを特定することは困難だが、可能性のあるIDパターンを検索

            # この部分は技術的に制限があるため、一般的なエラーハンドリングを行う
            logger.warning("⚠️ 画像URLからの直接ツイート特定は技術的に困難")

        # 方法3: より汎用的なリバース検索
        if SERPAPI_KEY and GoogleSearch:
            logger.info("🔍 汎用画像検索でTwitter関連情報探索中...")
            search = GoogleSearch({
                "engine": "google_images",
                "q": image_url,
                "api_key": SERPAPI_KEY,
                "tbs": "simg",
                "num": 20
            })

            results = search.get_dict()

            if "images_results" in results:
                for result in results["images_results"][:10]:
                    if "link" in result and "twitter.com" in result["link"]:
                        logger.info(f"🐦 リバース検索でツイートURL発見: {result['link']}")
                        tweet_content = get_x_tweet_content(result["link"])
                        if tweet_content:
                            return tweet_content

        logger.warning("⚠️ 画像からツイート内容を特定できませんでした")
        return None

    except Exception as e:
        logger.error(f"❌ 画像経由ツイート検索エラー: {str(e)}")
        return None

def judge_content_with_gemini(content: str) -> dict:
    """
    Gemini AIを使ってコンテンツを判定する
    """
    if not gemini_model:
        logger.error("❌ Gemini モデルが利用できません")
        return {"judgment": "！", "reason": "AI判定サービスが利用できません"}

    logger.info("🤖 Gemini AI判定開始")

    try:
        # Twitter画像の場合の特別処理
        if content.startswith("TWITTER_IMAGE:"):
            logger.info("🐦 Twitter画像URL（内容取得不可）の特別処理")
            return {
                "judgment": "？",
                "reason": "Twitter画像のため投稿内容を直接確認できません"
            }
        elif content.startswith("TWITTER_IMAGE_UNKNOWN:"):
            logger.info("🐦 Twitter画像URL（内容不明）の特別処理")
            return {
                "judgment": "？",
                "reason": "Twitter画像ですが投稿内容を特定できませんでした"
            }
        elif content.startswith("X投稿内容"):
            logger.info("🐦 X API経由で取得したツイート内容を分析")
            # 実際のツイート内容があるので、通常の判定を継続

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

        response = gemini_model.generate_content(prompt)
        result_text = response.text.strip()

        logger.info(f"📋 Gemini応答: {result_text}")

        # 応答を解析
        lines = result_text.strip().split('\n')
        judgment = "？"
        reason = "応答解析失敗"

        for line in lines:
            line = line.strip()
            if '判定：' in line or '判定:' in line:
                judgment_part = line.split('：')[1] if '：' in line else line.split(':')[1]
                judgment = judgment_part.replace('[','').replace(']','').strip()
                if judgment not in ['○', '×', '？']:
                    judgment = "？"
            elif '理由：' in line or '理由:' in line:
                reason_part = line.split('：')[1] if '：' in line else line.split(':')[1]
                reason = reason_part.replace('[','').replace(']','').strip()

        logger.info(f"✅ Gemini判定完了: {judgment} - {reason}")
        return {"judgment": judgment, "reason": reason}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Gemini API エラー: {error_msg}")

        # エラータイプに応じた分類
        if "not found" in error_msg.lower():
            return {"judgment": "！", "reason": "AIモデルが見つかりません"}
        elif "quota" in error_msg.lower() or "limit" in error_msg.lower():
            return {"judgment": "！", "reason": "API利用制限に達しました"}
        elif "auth" in error_msg.lower() or "permission" in error_msg.lower():
            return {"judgment": "！", "reason": "API認証エラーです"}
        elif "network" in error_msg.lower() or "timeout" in error_msg.lower():
            return {"judgment": "！", "reason": "ネットワークエラーです"}
        else:
            return {"judgment": "？", "reason": "AI判定処理でエラーが発生"}

def analyze_url_efficiently(url: str) -> Optional[Dict]:
    """
    URLを効率的に分析する
    1. 信頼できるニュースサイトは事前に○判定
    2. Twitter画像URLは特別処理
    3. その他はスクレイピング→Gemini判定
    """
    logger.info(f"🔄 URL分析開始: {url}")

    # 1. 信頼できるニュース・出版系ドメインの事前チェック
    if is_trusted_news_domain(url):
        logger.info(f"✅ 信頼ドメインのため事前○判定: {url}")
        return {
            "url": url,
            "judgment": "○",
            "reason": "信頼できるニュース・出版サイト"
        }

    # 2. Twitter画像URLの特別処理
    twitter_content = convert_twitter_image_to_tweet(url)
    if twitter_content:
        judgment_result = judge_content_with_gemini(twitter_content)
        return {
            "url": url,
            "judgment": judgment_result["judgment"],
            "reason": judgment_result["reason"]
        }

    # 3. 通常のスクレイピング→Gemini判定
    scraped_content = scrape_page_content(url)
    if not scraped_content:
        logger.info(f"  ❌ スクレイピング失敗: {url}")
        return None

    judgment_result = judge_content_with_gemini(scraped_content)
    logger.info(f"  ✅ 分析完了: {judgment_result['judgment']} - {judgment_result['reason']}")

    return {
        "url": url,
        "judgment": judgment_result["judgment"],
        "reason": judgment_result["reason"]
    }

def scrape_page_content(url: str) -> str | None:
    # 1. 拡張子とドメインで簡易フィルタリング
    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
    if any(url.lower().endswith(ext) for ext in image_extensions):
        logger.info(f"⏭️  画像拡張子のためスキップ: {url}")
        return None

    image_host_domains = ['m.media-amazon.com', 'img-cdn.theqoo.net']
    if any(domain in url for domain in image_host_domains):
        logger.info(f"⏭️  画像ホスティングドメインのためスキップ: {url}")
        return None

    # Twitter画像URLは特別処理（スクレイピングはスキップ）
    if 'pbs.twimg.com' in url:
        logger.info(f"🐦 Twitter画像URL検出のためスクレイピングスキップ: {url}")
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

        # 画像ハッシュを計算
        image_hash = calculate_image_hash(image_content)
        logger.info(f"🔑 画像ハッシュ計算完了: {image_hash[:16]}...")

        # Google Vision API WEB_DETECTIONでURL検索
        logger.info("🌐 Google Vision API WEB_DETECTION実行中...")
        url_list = search_web_for_image(image_content)

        logger.info(f"✅ Web検索完了: {len(url_list)}件のURLを発見")

        # 各URLを効率的に分析（ニュースサイトは事前○判定、Twitterは特別処理）
        processed_results = []

        for i, url in enumerate(url_list[:10]):  # 最大10件を処理
            logger.info(f"🔄 URL処理中 ({i+1}/{min(len(url_list), 10)}): {url}")

            # 効率的な分析実行
            result = analyze_url_efficiently(url)

            if result:
                processed_results.append(result)
                logger.info(f"  ✅ 処理完了: {result['judgment']} - {result['reason']}")
            else:
                # 分析失敗時
                processed_results.append({
                    "url": url,
                    "judgment": "？",
                    "reason": "分析に失敗しました"
                })
                logger.info(f"  ❌ 分析失敗: {url}")

        # 最終結果を保存
        search_results[image_id] = processed_results

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
        current_results = search_results.get(image_id, [])
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)