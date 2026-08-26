# scripts

## rakuten_price_check.py

楽天市場で競合価格を調べ、1本あたり単価で自社と比較して4段階判定するスクリプト。

### 使い方

```bash
# 判定結果を人が読む形で表示
python scripts/rakuten_price_check.py

# アプリの「調査結果のJSONを貼り付け」欄に貼れる形式で出力
python scripts/rakuten_price_check.py --json

# 除外された商品と除外理由も表示（絞り込みの調整用）
python scripts/rakuten_price_check.py --verbose
```

### 監視商品の設定

`_ローカル専用（共有しない）/監視商品.json` を編集する（このファイルは公開されない）。

```json
[
  {
    "name": "熊本イオン純天然水",
    "keyword": "天然水 500ml",
    "price": 2240,
    "unit_size": "500ml",
    "unit_count": 45
  }
]
```

| 項目 | 意味 |
|---|---|
| `name` | 商品名（表示用） |
| `keyword` | 楽天市場で検索するキーワード |
| `price` | 自社の販売価格（円） |
| `unit_size` | 1本あたりの容量（表示用） |
| `unit_count` | 本数。**単価計算に使うので正確に** |

### 鍵

`_ローカル専用（共有しない）/ここに鍵を置きます.md` から自動で読み込む。
スクリプト内に鍵は書かない（このリポジトリは公開されているため）。

---

## 実装上の重要な注意（実データ検証で判明したこと）

### 1. 「価格の安い順」で1位を取ってはいけない

素直に `sort=+itemPrice` で1位を取ると、**1本単位のバラ売り（¥30など）や
3〜6本の小容量セットばかり**が上位に来る。45本セットの競合は価格帯が
¥2,000前後なので、安い順では遥か後方に埋もれてしまう。

そのため **自社売価の60〜140%の価格帯に絞って検索**している
（`PRICE_LO_RATIO` / `PRICE_HI_RATIO`）。

### 2. 商品名から本数を読むのは精度が低い

楽天の商品名は表記がばらばらで、そのままでは誤読する。

- `catchcopy` の方が構造化されているため**優先して見る**
- `24本/48本` のような**選択式表記は除外する**（表示価格がどちらか判別できず、
  48本と誤読すると単価が半分に見えて誤アラートになる。実際に検証中に発生した）
- 本数が 20〜100 の範囲外は比較対象にしない（小容量セット・1本売りを排除）

### 3. Origin ヘッダーが必須

楽天の新APIは `Origin` ヘッダーが無いと **403** を返す。
アプリ登録時のドメイン（`https://taisei998.github.io`）を送っている。

サーバー間通信でも自分で付けられるヘッダーなので、
クラウドからの定期実行でも問題なく動く（当初懸念していた点は解消済み）。

### 4. リクエスト間隔

アプリ登録時に Expected QPS = 1 で申請しているため、
複数商品を回すときは2秒待機を入れている。連続実行すると **429** が返る。

### 5. API仕様（2026年に刷新済み）

| 項目 | 内容 |
|---|---|
| エンドポイント | `https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701` |
| 認証 | `applicationId`（UUID形式）＋ `accessKey`（`pk_` で始まる）の両方が必須 |
| accessKey の渡し方 | **クエリパラメータ**。ヘッダー方式は環境により通らないことがある |

旧仕様（`app.rakuten.co.jp/services/api/...` に19桁の数字IDだけ）は使えない。

---

## amazon_price_check.py

Amazonで競合価格を調べる、楽天版と同じ方針のスクリプト。

### 使い方

```bash
python scripts/amazon_price_check.py
python scripts/amazon_price_check.py --json
```

### 監視商品の設定

`監視商品.json` の `keyword` を楽天とAmazonで共用する。Amazon用に別の
キーワードを使いたい場合だけ `amazon_keyword` を追加すれば、そちらが優先される。

```json
{
  "name": "熊本イオン純天然水",
  "keyword": "天然水 500ml",
  "amazon_keyword": "天然水 500ml 45本",
  "price": 2250,
  "unit_size": "500ml×45本",
  "unit_count": 45,
  "unit_mode": "count"
}
```

### 鍵

`_ローカル専用（共有しない）/ここに鍵を置きます.md` の
`### Amazon SP-API` セクションから `Client ID` / `Client Secret` / `Refresh Token`
を読み込む。実行のたびにLWAでアクセストークンを発行する（有効期限1時間）。

### 実装上の重要な注意（実データ検証で判明したこと）

**1. 2段構成のAPI呼び出し**
楽天は検索結果に価格が含まれる1段構成だったが、Amazonは
`searchCatalogItems`（キーワード→ASIN）→ `getItemOffers`（ASIN→価格）の2段構成。
監視商品1件につき、ASINの数だけPricing APIを呼ぶ。

**2. Pricing APIのレート制限**
呼び出し間隔を1.2秒にしたところ、実際に **429 QuotaExceeded** が複数件発生した。
2.2秒に広げて解消。ただし正確な制限値は事前に分からないため、
429が返った場合は自動で待機・再試行する仕組みも入れてある（`_sp_api_get`の`retries`）。

**3. ロール選択**
Solution Provider Portalのアプリ登録画面で、キーワード検索
（`searchCatalogItems`）には「**商品の出品**」ロールが必要（「Catalog」という
名前の項目は無い）。価格取得（`getItemOffers`）には「**価格**」ロールが必要。

**4. 単価換算ロジックは楽天と共通化**
内容量の読み取り（`最大約10kg`の誤読対策、選択式表記の扱い等）は
`lib_unit_parser.py` に切り出し、楽天・Amazon両方から使っている。
片方だけ直して他方が古いままになる事故を防ぐため。
