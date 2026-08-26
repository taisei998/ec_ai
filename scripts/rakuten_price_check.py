"""
楽天市場 競合価格レポート

監視商品ごとに楽天市場を検索し、上位に出てくる商品の価格を一覧で報告する。

■ 設計方針（実データ検証を経ての判断）
「競合最安値を機械的に判定して🔴アラートを出す」方式は採用していない。
実データで検証したところ、生鮮食品では誤報が頻発したため。

  例：「とうもろこし 6本」で検索すると、エプソン互換インク（型番の通称が
      "とうもろこし"）、爽健美茶2L×6本、とうきびチョコ16本、うさぎ用
      ペットフードなどが大量に混ざり、「競合最安118.8円/本」＝北海道銘菓の
      チョコという結果になった。これでアラートを出せば完全な誤報になる。

  例：「みかん 1.5kg」では、比較可能と判定されたのが肥料3件とナタデココ1件。
      本物のみかんは「訳あり」除外に全部かかって消えた（自社商品自体が
      訳あり品なので、除外すべきワードではなかった）。

そのため、機械判定は行わず「検索上位の価格一覧」を届け、
判断は人が行う方式にしている。単価換算は参考値として併記する。

使い方:
    python rakuten_price_check.py              # 価格一覧を表示
    python rakuten_price_check.py --json       # JSON出力（アプリ貼り付け用）
    python rakuten_price_check.py --wide       # 価格帯の絞り込みを外して広く見る

鍵は _ローカル専用（共有しない）/ここに鍵を置きます.md から読む。
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

ENDPOINT = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
ORIGIN = "https://taisei998.github.io"   # 必須。無いと403

# 明らかに商材が違うものだけ落とす。判定しない方針なので最小限に留める。
# （「訳あり」等は競合そのものの可能性があるので入れない）
NOISE_WORDS = [
    "インクカートリッジ", "互換インク", "エプソン", "EPSON", "プリンター",
    "ポケモンカード", "ヴァイスシュヴァルツ", "トレーディングカード",
    "CD、音楽", "DVD", "Blu-ray", "コミック", "文庫",
    "ペット用", "うさぎ", "小動物", "犬用", "猫用", "ドッグ", "キャット",
    "肥料", "苗木", "培養土", "剪定", "替刃",
    "入浴剤", "スタンプ台", "マシュマロ",
]

PRICE_LO_RATIO, PRICE_HI_RATIO = 0.6, 1.4


def load_credentials():
    if not KEYFILE.exists():
        sys.exit(f"鍵ファイルが見つかりません: {KEYFILE}")
    text = KEYFILE.read_text(encoding="utf-8")

    def grab(label):
        m = re.search(label + r"\s*[:：]\s*(\S+)", text)
        return m.group(1).strip() if m else None

    app_id, access_key = grab("applicationId"), grab("accessKey")
    if not app_id or not access_key:
        sys.exit("applicationId / accessKey が読み取れません。")
    return app_id, access_key


def load_items():
    if not ITEMS_FILE.exists():
        sys.exit(f"監視商品リストがありません: {ITEMS_FILE}")
    try:
        items = json.loads(ITEMS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"監視商品.json の書式エラー（{e.lineno}行目付近）: {e.msg}")
    return [it for it in items if it.get("keyword") and it.get("price")]


def parse_unit(item, mode, count_hint):
    """商品(catchcopy優先→itemName)から単価を推定する薄いラッパー。実装は lib_unit_parser 共通。"""
    texts = [item.get("catchcopy"), item.get("itemName")]
    return _shared_parse_unit(item["itemPrice"], texts, mode, count_hint)


def search(app_id, access_key, keyword, min_price=None, max_price=None,
           genre_id=None, hits=30):
    params = {
        "applicationId": app_id,
        "accessKey": access_key,
        "keyword": keyword,
        "hits": hits,
        "sort": "+itemPrice",
        "format": "json",
    }
    if min_price:
        params["minPrice"] = min_price
    if max_price:
        params["maxPrice"] = max_price
    if genre_id:
        params["genreId"] = genre_id

    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Origin": ORIGIN,
        "User-Agent": "shinshouhin-navi-price-monitor/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(body)
            return None, f"HTTP {e.code} {j.get('error')}: {j.get('error_description')}"
        except Exception:
            return None, f"HTTP {e.code} {body[:150]}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def collect(item, app_id, access_key, wide=False):
    self_price = item["price"]
    mode = item.get("unit_mode", "count")
    count_hint = item.get("unit_count", 1)

    if wide:
        lo = hi = None
    else:
        lo = int(self_price * PRICE_LO_RATIO)
        hi = int(self_price * PRICE_HI_RATIO)

    data, err = search(app_id, access_key, item["keyword"], lo, hi,
                       item.get("genre_id"))
    if err:
        return {"error": err, "rows": []}

    raw = [w.get("Item", w) for w in data.get("Items", [])]
    rows, noise = [], 0
    seen = set()
    for it in raw:
        blob = normalize(it.get("itemName")) + " " + normalize(it.get("catchcopy"))
        if any(w in blob for w in NOISE_WORDS):
            noise += 1
            continue
        # 同一商品の重複出品をまとめる
        key = (it.get("itemName", "")[:40], it["itemPrice"])
        if key in seen:
            continue
        seen.add(key)

        unit, unit_label = parse_unit(it, mode, count_hint)
        rows.append({
            "price": it["itemPrice"],
            "name": it.get("itemName", ""),
            "shop": it.get("shopName", ""),
            "url": it.get("itemUrl", ""),
            "review_avg": it.get("reviewAverage"),
            "review_count": it.get("reviewCount"),
            "unit": unit,
            "unit_label": unit_label,
        })

    # 内容量が読めて比較できるものを上に、読めないものを下に。
    # それぞれの中では単価／価格の安い順にする。
    comparable = sorted([r for r in rows if r["unit"] is not None],
                        key=lambda r: r["unit"])
    others = sorted([r for r in rows if r["unit"] is None],
                    key=lambda r: r["price"])

    return {
        "error": None,
        "total": data.get("count", 0),
        "fetched": len(raw),
        "noise": noise,
        "rows": comparable + others,
        "comparable": len(comparable),
        "range": (lo, hi),
    }


def print_report(item, res):
    mode = item.get("unit_mode", "count")
    unit_name = "円/kg" if mode == "weight" else "円/個"
    self_price = item["price"]
    cnt = item.get("unit_count", 1)
    self_unit = self_price / cnt if cnt else None

    print("=" * 78)
    print(f"■ {item['name']}")
    print(f"   自社売価 ¥{self_price:,}"
          + (f"（{item.get('unit_size','')} / 参考 {self_unit:,.0f}{unit_name}）" if self_unit else ""))
    print(f"   検索キーワード「{item['keyword']}」")

    if res["error"]:
        print(f"   ⚠ 取得失敗: {res['error']}")
        print()
        return

    lo, hi = res["range"]
    band = f"¥{lo:,}〜¥{hi:,}" if lo else "指定なし（--wide）"
    print(f"   価格帯 {band} ／ 楽天の該当 {res['total']:,}件"
          f" ／ 表示 {len(res['rows'])}件（別商材 {res['noise']}件は除外）")
    print()

    if not res["rows"]:
        print("   該当なし。キーワードを見直すか --wide をお試しください。")
        print()
        return

    n_cmp = res.get("comparable", 0)
    shown = res["rows"][:15]

    if n_cmp:
        print(f"   ◆ 内容量が同等で比較できるもの（{n_cmp}件）"
              "　※単価の「?」は数量の選択肢が複数あり価格の対応が不確かなもの")
        print(f"   {'価格':>9}  {'単価':>13}  商品 / ショップ")
        print("   " + "-" * 71)
        for r in shown[:n_cmp]:
            u = f"{r['unit']:,.0f}{unit_name}（{r['unit_label']}）"
            print(f"   ¥{r['price']:>8,}  {u:>13}  {r['name'][:40]}")
            rv = f"  ★{r['review_avg']}({r['review_count']})" if r.get("review_count") else ""
            print(f"   {'':>9}  {'':>13}  └ {r['shop'][:26]}{rv}")
        print()

    rest = shown[n_cmp:]
    if rest:
        print(f"   ◇ 内容量が読めない・自社と大きく違うもの（参考）")
        print(f"   {'価格':>9}  商品 / ショップ")
        print("   " + "-" * 71)
        for r in rest:
            print(f"   ¥{r['price']:>8,}  {r['name'][:52]}")
            rv = f"  ★{r['review_avg']}({r['review_count']})" if r.get("review_count") else ""
            print(f"   {'':>9}  └ {r['shop'][:26]}{rv}")
        print()


def build_app_json(items, results):
    now = datetime.now(JST).isoformat(timespec="seconds")
    out = {"checkedAt": now, "results": []}
    for item, res in zip(items, results):
        entry = {
            "keyword": item["keyword"],
            "mall": "rakuten",
            "status": "unavailable" if res["error"] else "ok",
            "note": res["error"] or "楽天APIで取得（判定は行っていません。人が確認してください）",
            "competitors": [],
        }
        for rank, r in enumerate(res["rows"][:10], 1):
            entry["competitors"].append({
                "rank": rank,
                "isAd": False,
                "name": r["name"],
                "volume": r["unit_label"] or "",
                "price": r["price"],
                "shop": r["shop"],
                "url": r["url"],
                "shipping": "unknown",
                "coupon": "",
                "points": "",
            })
        out["results"].append(entry)
    return out


def main():
    ap = argparse.ArgumentParser(description="楽天市場の競合価格を一覧で取得する")
    ap.add_argument("--json", action="store_true", help="アプリ貼り付け用JSONを出力")
    ap.add_argument("--wide", action="store_true", help="価格帯の絞り込みを外す")
    args = ap.parse_args()

    app_id, access_key = load_credentials()
    items = load_items()
    if not items:
        sys.exit("監視商品が登録されていません。監視商品.json を確認してください。")

    results = []
    for i, item in enumerate(items):
        if i:
            time.sleep(2)   # 登録QPS=1に合わせる
        results.append(collect(item, app_id, access_key, args.wide))

    if args.json:
        print(json.dumps(build_app_json(items, results), ensure_ascii=False, indent=2))
        return

    print()
    print(f"楽天市場 競合価格レポート   {datetime.now(JST).strftime('%Y-%m-%d %H:%M')}")
    print("※このレポートは価格の一覧です。自動判定は行っていません。")
    print()
    for item, res in zip(items, results):
        print_report(item, res)

    print("=" * 78)
    print("価格を見て気になる商品があれば、URLを開いて実物を確認してください。")


if __name__ == "__main__":
    main()
