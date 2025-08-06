# SerpAPI統合実装 - 逆画像検索機能

この実装では、SerpAPIとパーセプチュアルハッシュマッチングを組み合わせた高精度逆画像検索機能を提供し、ほぼ同一の画像のみを返すようにしています。

## 🎯 目的

Google Vision APIのWeb検出は**完全一致**と**部分一致**の両方で高い精度を発揮しますが、**検出結果数に限りがある**ため、**SerpAPI** (`engine=google_reverse_image`) で補完します。

SerpAPIは時として無関係または不正確な一致を返すことがあります。精度を確保するため、入力画像と**ほぼ同一の画像にリンクされたURLのみ**を取得します。

## ✅ 実装機能

1. **SerpAPIを使用** (`engine=google_reverse_image`) して `visual_matches` を取得
2. **各サムネイル画像** をディスクに保存せずメモリ内で直接処理
3. **各サムネイルを比較** `imagehash` ライブラリの `phash` を使用して入力画像と比較
4. **ハッシュ距離が2以下の画像のみ** を「一致」として判定
5. **一致した結果から `link`（ページURL）を抽出** してJSONとして返す

## 📦 必要なライブラリ

```bash
pip install google-search-results imagehash requests Pillow
```

## 🔧 環境設定

SerpAPIキーを設定：
```bash
export SERPAPI_KEY="your_serpapi_key_here"
```

または `.env` ファイルに：
```
SERPAPI_KEY=your_serpapi_key_here
```

## 🚀 使用方法

### 基本的な使用方法

```python
from main import serpapi_reverse_image_search

# 画像をバイトとして読み込み
with open("your_image.jpg", "rb") as f:
    image_bytes = f.read()

# 検索実行
results = serpapi_reverse_image_search(image_bytes)

print(f"一致数: {len(results)}")
for result in results:
    print(f"URL: {result['url']}")
    print(f"ハッシュ距離: {result['hash_distance']}")
```

### 期待される出力形式

```json
[
    {
        "url": "https://example.com/magazine/cover",
        "search_method": "SerpAPI完全一致",
        "search_source": "SerpAPI Reverse Image",
        "score": 0.8,
        "confidence": "高",
        "hash_distance": 1,
        "title": "雑誌カバー",
        "thumbnail_url": "https://example.com/thumb.jpg"
    }
]
```

## 🔍 動作原理

1. **入力処理**: 入力画像のパーセプチュアルハッシュ（pHash）を計算
2. **SerpAPIクエリ**: `google_reverse_image` エンジンを使用してSerpAPIに画像をアップロード
3. **視覚的一致**: SerpAPIレスポンスから `visual_matches` を取得
4. **ハッシュ比較**: 各サムネイルに対して：
   - サムネイル画像をダウンロード（メモリ内処理）
   - サムネイルのpHashを計算
   - 入力画像とのハッシュ距離を比較
   - 距離≤2の一致のみを受け入れ
5. **結果抽出**: 一致した結果からページURLを抽出

## 📊 ハッシュ距離基準

- **距離 0**: 同一画像
- **距離 1-2**: ほぼ同一画像（採用）
- **距離 3+**: 異なる画像（除外）

## 🧪 テスト

テストスクリプトを実行：
```bash
python backend/test_serpapi_integration.py
```

または既存のアップロード機能でテスト：
```bash
curl -X POST "http://localhost:8000/upload" \
     -H "Content-Type: multipart/form-data" \
     -F "file=@your_image.jpg"
```

## 🔧 既存システムとの統合

実装は既存のFastAPIアプリケーションに統合されています：

- **関数**: `serpapi_reverse_image_search()`
- **統合先**: `enhanced_image_search_with_reverse()` 関数内で使用
- **フロー**: Vision API → SerpAPI → 従来逆検索 → 結果統合

## ⚠️ エラーハンドリング

実装では以下のエラーを処理します：
- APIキーの不足
- SerpAPIエラー
- 画像処理の失敗
- ネットワークタイムアウト
- 無効な画像形式

## 📈 パフォーマンス考慮事項

- サムネイルをメモリ内で処理（ディスクI/Oなし）
- 画像ダウンロードの設定可能なタイムアウト
- 一時ファイルの自動クリーンアップ
- imagehashライブラリによる効率的なハッシュ比較

## 🔒 セキュリティ

- 一時ファイルの自動クリーンアップ
- ブロッキング防止のためのUser-Agentヘッダー
- リクエストのハング防止のためのタイムアウト制限
- 画像データの入力検証

## 📊 統合後の検索方法分類

- **完全一致**: Vision API完全一致
- **部分一致**: Vision API部分一致
- **SerpAPI完全一致**: SerpAPIでハッシュ距離≤2の一致 ← **新機能**
- **関連ページ**: Vision API関連ページ
- **逆引き検索**: 従来の逆検索機能
- **テキスト検索**: 信頼度別テキスト検索