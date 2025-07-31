from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import json
import uuid
import base64
import re
import logging
import requests
from datetime import datetime
from typing import Dict, List, Optional
from io import BytesIO
from dotenv import load_dotenv
from PIL import Image
import serpapi
import httpx
from bs4 import BeautifulSoup
from google.cloud import vision

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

# 環境変数から各種API_KEYを取得
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

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

async def analyze_image_with_vision(image_path: str) -> Dict:
    """Google Vision APIを使って画像を分析する"""
    logger.info(f"🔍 Google Vision API画像分析開始: {image_path}")

    try:
        # Vision APIクライアントを初期化
        client = vision.ImageAnnotatorClient()

        # 画像を読み込み
        with open(image_path, 'rb') as image_file:
            content = image_file.read()
        image = vision.Image(content=content)

        # テキスト検出
        text_response = client.text_detection(image=image)
        texts = text_response.text_annotations

        # オブジェクト検出
        objects_response = client.object_localization(image=image)
        objects = objects_response.localized_object_annotations

        # ラベル検出
        labels_response = client.label_detection(image=image)
        labels = labels_response.label_annotations

        # 検出されたテキストを結合
        detected_text = ""
        if texts:
            detected_text = texts[0].description

        # オブジェクト名を収集
        detected_objects = [obj.name for obj in objects]

        # ラベル名を収集
        detected_labels = [label.description for label in labels]

        logger.info(f"📝 検出テキスト: {detected_text[:100] if detected_text else 'なし'}")
        logger.info(f"🎯 検出オブジェクト: {detected_objects}")
        logger.info(f"🏷️ 検出ラベル: {detected_labels}")

        # 書籍・漫画関連の判定
        is_book_related = False
        suspicious_keywords = []

        # 検出されたテキスト・オブジェクト・ラベルを全て確認
        all_detected_content = (detected_text + " " + " ".join(detected_objects) + " " + " ".join(detected_labels)).lower()

        # 書籍・漫画関連キーワード
        book_keywords = ["book", "manga", "comic", "novel", "text", "page", "chapter", "読む", "本", "漫画", "小説"]
        is_book_related = any(keyword in all_detected_content for keyword in book_keywords)

        # 違法・疑わしいキーワード
        illegal_keywords = ["無料", "free", "download", "違法", "コピー", "raw", "torrent", "piracy"]
        for keyword in illegal_keywords:
            if keyword in all_detected_content:
                suspicious_keywords.append(keyword)

        logger.info(f"📚 書籍関連: {is_book_related}")
        logger.info(f"⚠️ 疑わしいキーワード: {suspicious_keywords}")

        return {
            "detected_text": detected_text,
            "detected_objects": detected_objects,
            "detected_labels": detected_labels,
            "is_book_related": is_book_related,
            "suspicious_keywords": suspicious_keywords,
            "analysis_success": True,
            "debug_info": {
                "all_detected_content": all_detected_content,
                "book_keywords_found": [k for k in book_keywords if k in all_detected_content],
                "illegal_keywords_found": [k for k in illegal_keywords if k in all_detected_content]
            }
        }

    except Exception as e:
        logger.error(f"❌ Vision API分析エラー: {str(e)}")
        return {
            "detected_text": "",
            "detected_objects": [],
            "detected_labels": [],
            "is_book_related": False,
            "suspicious_keywords": [],
            "analysis_success": False,
            "error": str(e)
        }

async def check_domain_and_analyze(url: str, domain: str) -> Dict[str, str]:
    """ドメインを分析し、必要に応じてHTMLを取得して内容を分析する"""

    logger.info(f"🔍 ドメイン分析開始: {domain}")

    # 公式ドメインチェック
    is_official = any(official_domain in domain.lower() for official_domain in OFFICIAL_DOMAINS)

    if is_official:
        logger.info(f"✅ 公式ドメインを検出: {domain}")
        return {
            "status": "safe",
            "reason": "公式ドメインです",
            "content_analysis": None
        }

    logger.info(f"🌐 HTML取得開始: {url}")

    # 非公式の場合、HTMLを取得して分析
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            # User-Agentを設定してアクセス
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }

            logger.info(f"📡 HTTP リクエスト送信: {url}")
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            logger.info(f"✅ HTTP レスポンス受信: {response.status_code}, {len(response.text)} chars")

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

            logger.info(f"📝 テキスト抽出完了: {len(text)} chars")

            # X(Twitter)の特別処理
            if 'twitter.com' in domain or 'x.com' in domain:
                logger.info("🐦 Twitter/X特別処理")
                return await analyze_twitter_content(text, url)

            # 悪用判定
            logger.info("🔍 コンテンツ分析開始")
            result = analyze_content_for_violations(text, domain)
            logger.info(f"✅ 分析完了: {result['status']} - {result['reason']}")
            return result

    except httpx.TimeoutException:
        logger.error(f"⏰ タイムアウト: {url}")
        return {
            "status": "unknown",
            "reason": "サイトへのアクセスがタイムアウトしました",
            "content_analysis": None
        }
    except httpx.HTTPStatusError as e:
        logger.error(f"🌐 HTTP エラー: {e.response.status_code} for {url}")
        return {
            "status": "unknown",
            "reason": f"HTTP エラー: {e.response.status_code}",
            "content_analysis": None
        }
    except Exception as e:
        logger.error(f"❌ 分析エラー: {str(e)} for {url}")
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

async def analyze_and_judge_image(image_path: str) -> Dict:
    """画像を分析して○×判定を行う"""
    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="指定された画像ファイルが見つかりません")

    logger.info(f"🔍 画像分析・判定開始: {image_path}")

    try:
        # Google Vision APIで画像分析
        vision_result = await analyze_image_with_vision(image_path)

        if not vision_result["analysis_success"]:
            logger.error("❌ Vision API分析に失敗")
            return {
                "judgment": "×",
                "reason": "画像分析に失敗しました",
                "details": vision_result.get("error", "不明なエラー"),
                "confidence": 0
            }

        # 書籍関連でない場合は対象外
        if not vision_result["is_book_related"]:
            logger.info("📚 書籍関連ではない画像")
            return {
                "judgment": "○",
                "reason": "書籍・漫画に関連しない画像のため問題なし",
                "details": f"検出内容: {', '.join(vision_result['detected_labels'][:3])}",
                "confidence": 0.9
            }

        # 疑わしいキーワードがある場合は×
        if vision_result["suspicious_keywords"]:
            logger.warning(f"⚠️ 疑わしいキーワード検出: {vision_result['suspicious_keywords']}")
            return {
                "judgment": "×",
                "reason": f"違法・疑わしいキーワードが検出されました: {', '.join(vision_result['suspicious_keywords'])}",
                "details": vision_result["detected_text"][:200] if vision_result["detected_text"] else "テキスト検出なし",
                "confidence": 0.8
            }

        # 書籍関連だが疑わしいキーワードなし
        logger.info("✅ 書籍関連だが問題なし")
        return {
            "judgment": "○",
            "reason": "書籍・漫画関連の画像ですが、問題となるキーワードは検出されませんでした",
            "details": vision_result["detected_text"][:200] if vision_result["detected_text"] else "テキスト検出なし",
            "confidence": 0.7,
            "debug_analysis": {
                "vision_result": vision_result,
                "detected_objects": vision_result["detected_objects"],
                "detected_labels": vision_result["detected_labels"],
                "detected_text": vision_result["detected_text"],
                "suspicious_keywords_found": vision_result["suspicious_keywords"],
                "book_keywords_matched": vision_result.get("debug_info", {}).get("book_keywords_found", [])
            }
        }

    except Exception as e:
        logger.error(f"❌ 画像分析・判定エラー: {str(e)}")
        return {
            "judgment": "×",
            "reason": f"分析中にエラーが発生しました: {str(e)}",
            "details": "",
            "confidence": 0
        }

# 不要な関数は削除されました

async def search_with_google_custom_search(image_path: str) -> List[Dict]:
    """Google Custom Search APIを使った実際の画像関連検索"""
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        logger.warning("⚠️ Google API設定が不完全、スキップ")
        return []

    try:
        logger.info("🔍 Google Custom Search API検索開始")

        # 日本の書籍・漫画関連の海賊版サイトを優先検索
        search_queries = [
            "漫画 無料 ダウンロード 違法 サイト site:*.jp",
            "ライトノベル raw 無料 ダウンロード site:*.jp",
            "本 電子書籍 違法 ダウンロード 海賊版",
            "manga raw download 日本語",
            "漫画村 類似 サイト 違法"
        ]

        processed_results = []

        for query in search_queries:
            logger.info(f"🔍 検索クエリ: {query}")

            search_url = "https://www.googleapis.com/customsearch/v1"
            params = {
                "key": GOOGLE_API_KEY,
                "cx": GOOGLE_CSE_ID,
                "q": query,
                "num": 5,
                "safe": "off",
                "lr": "lang_ja",  # 日本語のページを優先
                "gl": "jp"        # 日本からの検索として実行
            }

            async with httpx.AsyncClient() as client:
                response = await client.get(search_url, params=params)
                if response.status_code == 200:
                    data = response.json()
                    logger.info(f"📋 Google検索結果: {len(data.get('items', []))}件")

                    for item in data.get('items', [])[:3]:  # 各クエリから最大3件
                        url = item.get('link', '')
                        title = item.get('title', 'タイトル不明')
                        snippet = item.get('snippet', '')

                        if url:
                            domain = url.split('/')[2] if '/' in url else url

                            # 実際のドメイン分析を実行
                            detailed_analysis = await check_domain_and_analyze(url, domain)

                            processed_results.append({
                                "url": url,
                                "domain": domain,
                                "title": title[:100],  # タイトルを制限
                                "source": f"Google検索: {query[:30]}",
                                "is_official": detailed_analysis["status"] == "safe",
                                "threat_level": detailed_analysis["status"],
                                "detailed_analysis": detailed_analysis,
                                "thumbnail": "",
                                "analysis_timestamp": datetime.now().isoformat(),
                                "snippet": snippet[:200]  # スニペットを制限
                            })

        # 重複URLを除去
        unique_results = []
        seen_urls = set()

        for result in processed_results:
            if result["url"] not in seen_urls:
                seen_urls.add(result["url"])
                unique_results.append(result)

        logger.info(f"✅ Google検索処理完了: {len(unique_results)}件（重複除去後）")
        return unique_results[:10]  # 最大10件

    except Exception as e:
        logger.error(f"❌ Google Custom Search API エラー: {str(e)}")
        return []

async def search_based_on_image_features(image_path: str) -> List[Dict]:
    """画像の特徴から推測した実際のWeb検索"""
    logger.info("🎯 画像特徴ベース検索開始")

    try:
        # 画像の基本情報を分析
        filename = os.path.basename(image_path)

        # 実際のWeb検索（海賊版関連サイト検索）
        piracy_sites = [
            "mangafreak.net",
            "mangadex.org",
            "mangaraw.to",
            "rawmanga.top",
            "novelupdates.com"
        ]

        processed_results = []

        for site in piracy_sites[:5]:  # 最初の5つのサイトをチェック
            logger.info(f"🔍 サイト分析: {site}")

            # サイトのURLを構築
            url = f"https://{site}"

            try:
                # 実際のドメイン分析を実行
                detailed_analysis = await check_domain_and_analyze(url, site)

                processed_results.append({
                    "url": url,
                    "domain": site,
                    "title": f"{site} - 海賊版サイト検査結果",
                    "source": "実際のサイト分析",
                    "is_official": detailed_analysis["status"] == "safe",
                    "threat_level": detailed_analysis["status"],
                    "detailed_analysis": detailed_analysis,
                    "thumbnail": "",
                    "analysis_timestamp": datetime.now().isoformat()
                })

            except Exception as site_error:
                logger.warning(f"⚠️ サイト {site} の分析でエラー: {str(site_error)}")
                continue

        logger.info(f"✅ 特徴ベース検索完了: {len(processed_results)}件")
        return processed_results

    except Exception as e:
        logger.error(f"❌ 特徴ベース検索エラー: {str(e)}")
        return []

async def try_base64_serpapi_search(image_path: str) -> List[Dict]:
    """Base64エンコード方式でSerpAPI検索を実行"""
    try:
        logger.info("🔍 Base64方式でSerpAPI検索開始")

        # 画像をBase64エンコード
        encoded_image = encode_image_to_base64(image_path)
        logger.info(f"📸 画像Base64エンコード完了: {len(encoded_image)} chars")

        client = serpapi.Client(api_key=SERPAPI_KEY)
        search_params = {
            "engine": "google_reverse_image",
            "image_url": f"data:image/jpeg;base64,{encoded_image}",
            "hl": "ja",
            "gl": "jp"
        }

        logger.info("🌐 SerpAPI Base64検索実行中...")
        results = client.search(search_params)

        # デバッグ: レスポンス構造を確認
        logger.info(f"📋 SerpAPIレスポンスキー: {list(results.keys())}")

        # 各キーの内容を詳細ログ出力
        for key, value in results.items():
            if isinstance(value, list):
                logger.info(f"📋 {key}: {len(value)}個のアイテム")
            elif isinstance(value, dict):
                logger.info(f"📋 {key}: 辞書型 ({len(value)}個のキー)")
            else:
                logger.info(f"📋 {key}: {type(value).__name__} - {str(value)[:100]}")

        # 検索クレジット情報を確認
        if 'search_metadata' in results:
            metadata = results['search_metadata']
            logger.info(f"🔍 検索メタデータ: {metadata}")

        # エラー情報を確認
        if 'error' in results:
            logger.error(f"❌ SerpAPIエラー: {results['error']}")
            return []

        processed_results = []

        # 画像検索結果を処理
        if "image_results" in results and results["image_results"]:
            logger.info(f"✅ image_results発見: {len(results['image_results'])}件")
            for item in results["image_results"][:10]:
                url = item.get("link", "")
                title = item.get("title", "")
                source = item.get("source", "")

                if url and title:
                    domain, is_official, basic_threat_level = analyze_domain(url)

                    try:
                        detailed_analysis = await check_domain_and_analyze(url, domain)
                    except Exception as e:
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

        # インライン画像結果も処理
        if "inline_images" in results and results["inline_images"]:
            logger.info(f"✅ inline_images発見: {len(results['inline_images'])}件")
            for item in results["inline_images"][:5]:
                url = item.get("link", "")
                title = item.get("title", "")
                source = item.get("source", "")

                if url and title:
                    domain, is_official, basic_threat_level = analyze_domain(url)

                    try:
                        detailed_analysis = await check_domain_and_analyze(url, domain)
                    except Exception as e:
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

        logger.info(f"✅ Base64検索結果処理完了: {len(processed_results)}件")
        return processed_results

    except Exception as e:
        logger.error(f"❌ Base64 SerpAPI検索エラー: {str(e)}")
        logger.error(f"❌ エラー詳細: {type(e).__name__}")
        return []

async def try_serpapi_search(image_path: str) -> List[Dict]:
    """URL方式でSerpAPI検索を実行（フォールバック）"""
    try:
        filename = os.path.basename(image_path)
        ngrok_url = "https://a46d8d27d10b.ngrok-free.app"
        image_url = f"{ngrok_url}/temp-images/{filename}"

        logger.info(f"📸 URL方式SerpAPI検索: {image_url}")

        client = serpapi.Client(api_key=SERPAPI_KEY)
        search_params = {
            "engine": "google_reverse_image",
            "image_url": image_url,
            "hl": "ja",
            "gl": "jp"
        }

        results = client.search(search_params)
        logger.info(f"📋 URL方式レスポンス: {len(results.get('image_results', []))}件")

        processed_results = []

        # URL方式の結果を処理（Base64と同じロジック）
        if "image_results" in results and results["image_results"]:
            for item in results["image_results"][:10]:
                url = item.get("link", "")
                title = item.get("title", "")
                source = item.get("source", "")

                if url and title:
                    domain, is_official, basic_threat_level = analyze_domain(url)

                    try:
                        detailed_analysis = await check_domain_and_analyze(url, domain)
                    except Exception as e:
                        detailed_analysis = {
                            "status": "unknown",
                            "reason": f"分析エラー: {str(e)}",
                            "content_analysis": None
                        }

                    processed_results.append({
                        "url": url,
                        "domain": domain,
                        "title": title,
                        "source": f"{source} (URL)",
                        "is_official": is_official,
                        "threat_level": basic_threat_level,
                        "detailed_analysis": detailed_analysis,
                        "thumbnail": item.get("thumbnail", ""),
                        "analysis_timestamp": datetime.now().isoformat()
                    })

        return processed_results

    except Exception as e:
        logger.error(f"❌ URL SerpAPI検索エラー: {str(e)}")
        logger.error(f"❌ エラー詳細: {type(e).__name__}")
        return []

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
async def analyze_image(image_id: str):
    """指定された画像IDに対してGoogle Vision API分析を実行し○×判定する"""

    logger.info(f"🔍 画像分析開始: image_id={image_id}")

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

    logger.info(f"📁 分析対象画像: {image_path}")

    try:
        # Google Vision APIで分析・判定
        logger.info("🤖 Google Vision API分析開始")
        judgment_result = await analyze_and_judge_image(image_path)
        logger.info(f"✅ 分析完了: 判定={judgment_result['judgment']}")

        # 分析結果をメモリに保存
        search_results[image_id] = [judgment_result]

        # アップロード記録を更新
        record["analysis_status"] = "completed"
        record["analysis_time"] = datetime.now().isoformat()
        record["judgment"] = judgment_result["judgment"]
        record["reason"] = judgment_result["reason"]
        record["confidence"] = judgment_result.get("confidence", 0)
        save_records()

        logger.info(f"✅ 分析完了: image_id={image_id}, 判定={judgment_result['judgment']}")

        return {
            "success": True,
            "image_id": image_id,
            "judgment": judgment_result["judgment"],
            "reason": judgment_result["reason"],
            "details": judgment_result.get("details", ""),
            "confidence": judgment_result.get("confidence", 0),
            "analysis_time": record["analysis_time"],
            "message": f"分析が完了しました。判定: {judgment_result['judgment']}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 分析エラー: {str(e)}")

        # エラー状態を記録
        record["analysis_status"] = "failed"
        record["analysis_error"] = str(e)
        record["analysis_time"] = datetime.now().isoformat()
        save_records()

        raise HTTPException(
            status_code=500,
            detail={
                "error": "analysis_failed",
                "message": f"画像分析中にエラーが発生しました: {str(e)}",
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
            detail="指定されたimage_idが見つかりません。"
        )

    record = upload_records[image_id]

    if record.get("analysis_status") != "completed":
        raise HTTPException(
            status_code=404,
            detail="指定された画像の分析結果がありません。先に分析を実行してください。"
        )

    return {
        "success": True,
        "image_id": image_id,
        "original_filename": record.get("original_filename", "不明"),
        "judgment": record.get("judgment", "×"),
        "reason": record.get("reason", "分析結果不明"),
        "confidence": record.get("confidence", 0),
        "analysis_time": record.get("analysis_time", "不明"),
        "file_size": record.get("file_size", 0),
        "message": f"判定結果: {record.get('judgment', '×')}"
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
    """指定されたドメインの判定テストを実行する"""
    logger.info(f"🧪 ドメインテスト開始: {domain}")

    # テスト用URL
    test_url = f"https://{domain}"

    try:
        # ドメイン分析を実行
        result = await check_domain_and_analyze(test_url, domain)

        # 基本的な脅威レベル評価も取得
        _, is_official, basic_threat_level = analyze_domain(test_url)

        logger.info(f"✅ ドメインテスト完了: {domain} -> {result['status']}")

        return {
            "success": True,
            "domain": domain,
            "test_url": test_url,
            "is_official": is_official,
            "basic_threat_level": basic_threat_level,
            "detailed_analysis": result,
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
            "google_api_key": GOOGLE_API_KEY is not None,
            "google_cse_id": GOOGLE_CSE_ID is not None,
            "gemini_api_key": GEMINI_API_KEY is not None,
            "serpapi_key": SERPAPI_KEY is not None
        },
        "official_domains_count": len(OFFICIAL_DOMAINS),
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