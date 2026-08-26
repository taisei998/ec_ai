# -*- coding: utf-8 -*-
"""
Amazon 競合価格レポート

監視商品ごとにAmazon.co.jpを検索し、上位に出てくる商品の価格を一覧で報告する。
楽天版（rakuten_price_check.py）と同じ「判定はせず一覧を届ける」方針。
理由は同じく、キーワード検索には無関係な商品が混ざり得るため
（実データ検証は楽天で行ったが、Amazonの商品検索も同種の性質を持つ）。

■ Amazon特有の事情（実装上の注意）
楽天は「キーワード検索結果に価格が含まれる」1段構成だったが、Amazonは
「①キーワードでASINを探す（Catalog Items API） → ②そのASINの価格を取る
（Product Pricing API）」の2段構成。商品1件のレポートに複数回のAPI呼び出しが
発生するため、監視商品を増やすと呼び出し回数が増える点に注意。

■ 認証
LWA の Refresh Token からアクセストークンを都度発行する（有効期限1時間）。
Client ID / Client Secret / Refresh Token は
_ローカル専用（共有しない）/ここに鍵を置きます.md から読む。

使い方:
    python amazon_price_check.py              # 価格一覧を表示
    python amazon_price_check.py --json       # JSON出力（アプリ貼り付け用）
"""

import re
import sys
import json
import time
import pathlib
import argparse
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

from lib_unit_parser import normalize, parse_unit as _shared_parse_unit

JST = timezone(timedelta(hours=9))

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "_ローカル専用（共有しない）"
KEYFILE = PRIVATE / "ここに鍵を置きます.md"
ITEMS_FILE = PRIVATE / "監視商品.json"

LWA_URL = "https://api.amazon.com/auth/o2/token"
SP_API_HOST = "https://sellingpartnerapi-fe.amazon.com"   # 東京リージョン
MARKETPLACE_ID = "A1VC38T7YXB528"                          # Amazon.co.jp

CATALOG_PAGE_SIZE = 10     # 1商品あたり最大何件のASINを見るか
PRICING_SLEEP_SEC = 2.2    # Pricing APIの呼び出し間隔（レート制限対策。1.2秒では429が発生したため）

# 明らかに商材が違うものだけ落とす（楽天版と同じ方針・最小限）
NOISE_WORDS = [
    "インクカートリッジ", "互換インク", "エプソン", "EPSON", "プリンター",
    "ポケモンカード", "トレーディングカード",
    "CD、音楽", "DVD", "Blu-ray", "コミック", "文庫",
    "ペット用", "うさぎ", "小動物", "犬用", "猫用", "ドッグ", "キャット",
    "肥料", "苗木", "培養土", "剪定", "替刃",
]


def load_credentials():
    if not KEYFILE.exists():
        sys.exit(f"鍵ファイルが見つかりません: {KEYFILE}")
    text = KEYFILE.read_text(encoding="utf-8")

    m = re.search(r"###\s*Amazon SP-API(.*?)(?=###|\Z)", text, re.S)
    section = m.group(1) if m else ""

    def grab(label):
        m2 = re.search(re.escape(label) + r"\s*[:：]\s*(\S+)", section)
        return m2.group(1).strip() if m2 else None

    client_id = grab("Client ID")
    client_secret = grab("Client Secret")
    refresh_token = grab("Refresh Token")
    if not all([client_id, client_secret, refresh_token]):
        sys.exit("Amazon の Client ID / Client Secret / Refresh Token が読み取れません。")
    return client_id, client_secret, refresh_token


def load_items():
    if not ITEMS_FILE.exists():
        sys.exit(f"監視商品リストがありません: {ITEMS_FILE}")
    try:
        items = json.loads(ITEMS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"監視商品.json の書式エラー（{e.lineno}行目付近）: {e.msg}")
    # amazon_keyword があればそちらを優先、無ければ keyword（楽天と共用）を使う
    out = []
    for it in items:
        kw = it.get("amazon_keyword") or it.get("keyword")
        if kw and it.get("price"):
            merged = dict(it)
            merged["_amazon_keyword"] = kw
            out.append(merged)
    return out


def get_access_token(client_id, client_secret, refresh_token):
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    req = urllib.request.Request(LWA_URL, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))["access_token"]


def _sp_api_get(access_token, path, params, retries=3):
    """
    429（レート制限超過）が返った場合、少し待って自動リトライする。
    Amazonのレート制限は正確な値が事前に分からないため、固定間隔での
    呼び出しだけに頼らず、実際に制限に当たった場合の回復手段を持たせる。
    """
    url = SP_API_HOST + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "x-amz-access-token": access_token,
        "User-Agent": "shinshouhin-navi-price-monitor/1.0",
    })
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            body = e.read().decode("utf-8", errors="replace")
            try:
                j = json.loads(body)
                errs = j.get("errors") or [{}]
                return None, "HTTP " + str(e.code) + " " + str(errs[0].get("code")) + ": " + str(errs[0].get("message"))
            except Exception:
                return None, "HTTP " + str(e.code) + " " + body[:150]
        except Exception as e:
            return None, type(e).__name__ + ": " + str(e)


def search_catalog(access_token, keyword, page_size=CATALOG_PAGE_SIZE):
    return _sp_api_get(access_token, "/catalog/2022-04-01/items", {
        "keywords": keyword,
        "marketplaceIds": MARKETPLACE_ID,
        "includedData": "summaries",
        "pageSize": page_size,
    })


def get_offers(access_token, asin):
    return _sp_api_get(
        access_token,
        "/products/pricing/v0/items/" + asin + "/offers",
        {"MarketplaceId": MARKETPLACE_ID, "ItemCondition": "New"},
    )


def parse_unit(price, name, mode, count_hint):
    return _shared_parse_unit(price, [name], mode, count_hint)


def collect(item, access_token):
    keyword = item["_amazon_keyword"]
    mode = item.get("unit_mode", "count")
    count_hint = item.get("unit_count", 1)

    cat_data, err = search_catalog(access_token, keyword)
    if err:
        return {"error": err, "rows": []}

    raw_items = cat_data.get("items", [])
    rows = []
    noise = 0

    for i, it in enumerate(raw_items):
        asin = it.get("asin")
        summaries = it.get("summaries") or []
        name = summaries[0].get("itemName", "") if summaries else ""

        if any(w in name for w in NOISE_WORDS):
            noise += 1
            continue

        if i:
            time.sleep(PRICING_SLEEP_SEC)   # Pricing APIのレート制限に配慮
        offer_data, offer_err = get_offers(access_token, asin)
        if offer_err:
            rows.append({
                "price": None, "name": name, "asin": asin,
                "url": "https://www.amazon.co.jp/dp/" + asin,
                "note": "価格取得失敗: " + offer_err,
                "has_buybox": False, "unit": None, "unit_label": None,
            })
            continue

        payload = offer_data.get("payload", {})
        lowest_list = payload.get("Summary", {}).get("LowestPrices") or [{}]
        lowest = lowest_list[0]
        price = lowest.get("LandedPrice", {}).get("Amount")
        offers = payload.get("Offers", [])
        buybox_winner = None
        for o in offers:
            if o.get("IsBuyBoxWinner"):
                buybox_winner = o
                break
        n_offers = len(offers)

        if price is None:
            rows.append({
                "price": None, "name": name, "asin": asin,
                "url": "https://www.amazon.co.jp/dp/" + asin,
                "note": "出品なし（在庫切れの可能性）",
                "has_buybox": False, "unit": None, "unit_label": None,
            })
            continue

        unit, unit_label = parse_unit(price, name, mode, count_hint)
        rows.append({
            "price": price,
            "name": name,
            "asin": asin,
            "url": "https://www.amazon.co.jp/dp/" + asin,
            "n_offers": n_offers,
            "has_buybox": buybox_winner is not None,
            "unit": unit,
            "unit_label": unit_label,
            "note": None,
        })

    comparable = [r for r in rows if r.get("unit") is not None]
    comparable.sort(key=lambda r: r["unit"])
    others = [r for r in rows if r.get("unit") is None and r.get("price") is not None]
    others.sort(key=lambda r: r["price"])
    failed = [r for r in rows if r.get("price") is None]

    return {
        "error": None,
        "fetched": len(raw_items),
        "noise": noise,
        "rows": comparable + others + failed,
        "comparable": len(comparable),
    }


def print_report(item, res):
    mode = item.get("unit_mode", "count")
    unit_name = "円/kg" if mode == "weight" else "円/個"
    self_price = item["price"]
    cnt = item.get("unit_count", 1)
    self_unit = self_price / cnt if cnt else None

    print("=" * 78)
    print("■ " + item["name"])
    line = "   自社売価 ¥{:,}".format(self_price)
    if self_unit:
        line += "（" + item.get("unit_size", "") + " / 参考 {:,.0f}{}）".format(self_unit, unit_name)
    print(line)
    print("   検索キーワード「" + item["_amazon_keyword"] + "」")

    if res["error"]:
        print("   ⚠ 取得失敗: " + res["error"])
        print()
        return

    print("   Amazon 取得 {}件（別商材 {}件は除外）".format(res["fetched"], res["noise"]))
    print()

    if not res["rows"]:
        print("   該当なし。キーワードを見直してください。")
        print()
        return

    n_cmp = res.get("comparable", 0)
    shown = res["rows"]

    if n_cmp:
        print("   ◆ 内容量が同等で比較できるもの（{}件）".format(n_cmp)
              + "　※単価の「?」は数量の選択肢が複数あり価格の対応が不確かなもの")
        print("   {:>9}  {:>13}  商品".format("価格", "単価"))
        print("   " + "-" * 71)
        for r in shown[:n_cmp]:
            u = "{:,.0f}{}（{}）".format(r["unit"], unit_name, r["unit_label"])
            bb = "  ★BuyBox" if r.get("has_buybox") else ""
            print("   ¥{:>8,}  {:>13}  {}".format(r["price"], u, r["name"][:40]))
            print("   {:>9}  {:>13}  └ {}件の出品{}  {}".format("", "", r["n_offers"], bb, r["url"]))
        print()

    rest = [r for r in shown[n_cmp:] if r.get("price") is not None]
    if rest:
        print("   ◇ 内容量が読めない・自社と大きく違うもの（参考）")
        print("   {:>9}  商品".format("価格"))
        print("   " + "-" * 71)
        for r in rest:
            print("   ¥{:>8,}  {}".format(r["price"], r["name"][:52]))
            print("   {:>9}  └ {}".format("", r["url"]))
        print()

    failed = [r for r in shown if r.get("price") is None]
    if failed:
        print("   △ 価格が取得できなかったもの（{}件）".format(len(failed)))
        for r in failed:
            print("      {}  ({})".format(r["name"][:50], r["note"]))
        print()


def build_app_json(items, results):
    now = datetime.now(JST).isoformat(timespec="seconds")
    out = {"checkedAt": now, "results": []}
    for item, res in zip(items, results):
        entry = {
            "keyword": item["_amazon_keyword"],
            "mall": "amazon",
            "status": "unavailable" if res["error"] else "ok",
            "note": res["error"] or "Amazon SP-APIで取得（判定は行っていません。人が確認してください）",
            "competitors": [],
        }
        priced_rows = [x for x in res.get("rows", []) if x.get("price")][:10]
        for rank, r in enumerate(priced_rows, 1):
            entry["competitors"].append({
                "rank": rank,
                "isAd": False,
                "name": r["name"],
                "volume": r.get("unit_label") or "",
                "price": r["price"],
                "shop": "Amazon",
                "url": r["url"],
                "shipping": "unknown",
                "coupon": "",
                "points": "",
            })
        out["results"].append(entry)
    return out


def main():
    ap = argparse.ArgumentParser(description="Amazonの競合価格を一覧で取得する")
    ap.add_argument("--json", action="store_true", help="アプリ貼り付け用JSONを出力")
    args = ap.parse_args()

    client_id, client_secret, refresh_token = load_credentials()
    items = load_items()
    if not items:
        sys.exit("監視商品が登録されていません。監視商品.json を確認してください。")

    access_token = get_access_token(client_id, client_secret, refresh_token)

    results = []
    for i, item in enumerate(items):
        if i:
            time.sleep(1)
        results.append(collect(item, access_token))

    if args.json:
        print(json.dumps(build_app_json(items, results), ensure_ascii=False, indent=2))
        return

    print()
    print("Amazon 競合価格レポート   " + datetime.now(JST).strftime("%Y-%m-%d %H:%M"))
    print("※このレポートは価格の一覧です。自動判定は行っていません。")
    print()
    for item, res in zip(items, results):
        print_report(item, res)

    print("=" * 78)
    print("価格を見て気になる商品があれば、URLを開いて実物を確認してください。")


if __name__ == "__main__":
    main()
