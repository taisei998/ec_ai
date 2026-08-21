"""
楽天市場 競合価格チェック

監視商品ごとに楽天市場を検索し、1本あたり単価で競合と比較して4段階判定する。

使い方:
    python rakuten_price_check.py              # 判定結果を表示
    python rakuten_price_check.py --json       # JSON形式で出力（アプリ貼り付け用）
    python rakuten_price_check.py --verbose    # 除外された商品も表示（調整用）

鍵は _ローカル専用（共有しない）/ここに鍵を置きます.md から読む。
このスクリプト自体に鍵は書かない（GitHubで公開されるため）。
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

JST = timezone(timedelta(hours=9))

ROOT = pathlib.Path(__file__).resolve().parent.parent
KEYFILE = ROOT / "_ローカル専用（共有しない）" / "ここに鍵を置きます.md"
ITEMS_FILE = ROOT / "_ローカル専用（共有しない）" / "監視商品.json"

ENDPOINT = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
ORIGIN = "https://taisei998.github.io"   # アプリ登録時のドメイン。これが無いと403になる

# 比較対象にならない商品を除くためのキーワード
NG_WORDS = [
    "バラ売り", "1本単位", "破損", "訳あり", "お試し", "サンプル", "単品",
    "ペット用", "犬", "猫", "ドッグ", "キャット", "経口補水", "ギフト箱",
]

# 本数として妥当な範囲（これを外れるものは比較対象にしない）
COUNT_MIN, COUNT_MAX = 20, 100

# 検索する価格帯（自社売価に対する倍率）。
# 「安い順」だと3〜6本の小容量セットが上位を占め、目的の本数帯が埋もれるため、
# 自社と同じ価格帯を狙って取得する。
PRICE_LO_RATIO, PRICE_HI_RATIO = 0.6, 1.4


# ---------------------------------------------------------------- 鍵の読み込み

def load_credentials():
    if not KEYFILE.exists():
        sys.exit(f"鍵ファイルが見つかりません: {KEYFILE}")
    text = KEYFILE.read_text(encoding="utf-8")

    def grab(label):
        m = re.search(label + r"\s*[:：]\s*(\S+)", text)
        return m.group(1).strip() if m else None

    app_id = grab("applicationId")
    access_key = grab("accessKey")
    if not app_id or not access_key:
        sys.exit("applicationId / accessKey が読み取れません。鍵ファイルを確認してください。")
    return app_id, access_key


def load_items():
    """監視商品リスト。無ければ検証用のサンプルを1件返す。"""
    if ITEMS_FILE.exists():
        return json.loads(ITEMS_FILE.read_text(encoding="utf-8"))
    return [{
        "name": "熊本イオン純天然水",
        "keyword": "天然水 500ml",
        "price": 2240,
        "unit_size": "500ml",
        "unit_count": 45,
    }]


# ---------------------------------------------------------------- 本数の抽出

# 「24本/48本」「24本・48本」のように複数の本数が併記されている商品は、
# 表示価格がどちらのものか判別できないため比較対象にしない。
MULTI_CHOICE_RE = re.compile(r"\d{1,3}\s*本\s*[/・､、]\s*\d{1,3}\s*本")

COUNT_PATTERNS = [
    # 「500ml×48本」のようにサイズと本数が隣接する形。最も信頼できる
    (re.compile(r"\d{3,4}\s*(?:ml|mL|ML)\s*x?\s*(\d{1,3})\s*本"), 1),
    (re.compile(r"(?:計|合計)\s*(\d{1,3})\s*本"), 1),
    (re.compile(r"(\d{1,3})\s*本入"), 1),
    (re.compile(r"x\s*(\d{1,3})\s*本"), 1),
]


def normalize(s):
    return (s or "").replace("×", "x").replace("＊", "x").replace("*", "x").replace("／", "/")


def extract_count(item):
    """商品の本数を推定する。(本数, 情報源) を返す。読めなければ (None, 理由)。"""
    for field in ("catchcopy", "itemName"):
        raw = normalize(item.get(field))
        if not raw:
            continue
        if MULTI_CHOICE_RE.search(raw):
            return None, "本数が選択式（例: 24本/48本）のため価格が特定できない"
        for pat, group in COUNT_PATTERNS:
            m = pat.search(raw)
            if m:
                return int(m.group(group)), field
    return None, "本数が読み取れない"


# ---------------------------------------------------------------- API 呼び出し

def search_rakuten(app_id, access_key, keyword, min_price, max_price, hits=30):
    params = {
        "applicationId": app_id,
        "accessKey": access_key,      # クエリで渡す（ヘッダー方式は環境により通らない）
        "keyword": keyword,
        "hits": hits,
        "sort": "+itemPrice",
        "minPrice": min_price,
        "maxPrice": max_price,
        "format": "json",
    }
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Origin": ORIGIN,             # 必須。無いと 403 になる
        "User-Agent": "shinshouhin-navi-price-monitor/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(body)
            detail = f"{j.get('error')}: {j.get('error_description')}"
        except Exception:
            detail = body[:200]
        return None, f"HTTP {e.code} {detail}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- 判定

def evaluate(item, app_id, access_key, verbose=False):
    self_price = item["price"]
    self_count = item["unit_count"]
    self_unit = self_price / self_count

    lo = int(self_price * PRICE_LO_RATIO)
    hi = int(self_price * PRICE_HI_RATIO)

    data, err = search_rakuten(app_id, access_key, item["keyword"], lo, hi)
    if err:
        return {"status": "error", "message": err, "self_unit": self_unit,
                "competitors": [], "excluded": []}

    raw_items = [w.get("Item", w) for w in data.get("Items", [])]
    competitors, excluded = [], []

    for it in raw_items:
        blob = normalize(it.get("itemName")) + " " + normalize(it.get("catchcopy"))
        ng = next((w for w in NG_WORDS if w in blob), None)
        if ng:
            excluded.append((it, f"除外ワード「{ng}」"))
            continue

        count, src = extract_count(it)
        if count is None:
            excluded.append((it, src))
            continue
        if not (COUNT_MIN <= count <= COUNT_MAX):
            excluded.append((it, f"本数 {count} が対象範囲({COUNT_MIN}〜{COUNT_MAX})外"))
            continue

        competitors.append({
            "unit_price": it["itemPrice"] / count,
            "price": it["itemPrice"],
            "count": count,
            "name": it.get("itemName", ""),
            "shop": it.get("shopName", ""),
            "url": it.get("itemUrl", ""),
            "review_avg": it.get("reviewAverage"),
            "review_count": it.get("reviewCount"),
            "source": src,
        })

    competitors.sort(key=lambda c: c["unit_price"])

    if not competitors:
        level = "nodata"
    else:
        lowest = competitors[0]["unit_price"]
        # 0.1円/本の差は誤差として同値扱いにする
        if round(lowest, 1) < round(self_unit, 1):
            level = "alert"
        elif round(lowest, 1) == round(self_unit, 1):
            level = "tie"
        else:
            level = "lead"

    return {
        "status": "ok",
        "level": level,
        "self_unit": self_unit,
        "total_hits": data.get("count", 0),
        "fetched": len(raw_items),
        "competitors": competitors,
        "excluded": excluded,
    }


LEVEL_MARK = {
    "alert": "🔴 競合が下回る",
    "tie":   "🟡 同値で並走",
    "lead":  "🟢 自社が最安",
    "nodata": "○ データ不足",
}


def print_report(item, result, verbose=False):
    print("=" * 74)
    print(f"■ {item['name']}")
    print(f"  自社: ¥{item['price']:,} / {item['unit_size']}×{item['unit_count']}本"
          f" = {result['self_unit']:.1f}円/本")
    print(f"  検索キーワード: 「{item['keyword']}」")

    if result["status"] == "error":
        print(f"  ⚠ 取得失敗: {result['message']}")
        return

    print(f"  楽天の該当件数: {result['total_hits']:,}件"
          f" / 取得 {result['fetched']}件"
          f" / 比較可能 {len(result['competitors'])}件")
    print()
    print(f"  判定: {LEVEL_MARK[result['level']]}")

    comps = result["competitors"]
    if comps:
        top = comps[0]
        diff = result["self_unit"] - top["unit_price"]
        print(f"  競合最安: {top['unit_price']:.1f}円/本"
              f"（¥{top['price']:,} / {top['count']}本）")
        if diff > 0:
            print(f"  価格差: 競合が {diff:.1f}円/本 安い"
                  f"（1本換算・{item['unit_count']}本換算で ¥{diff * item['unit_count']:,.0f} 相当）")
        elif diff < 0:
            print(f"  価格差: 自社が {-diff:.1f}円/本 安い")
        print(f"  該当商品: {top['name'][:58]}")
        print(f"  ショップ: {top['shop']}"
              + (f"  ★{top['review_avg']}（{top['review_count']}件）" if top.get("review_count") else ""))
        print(f"  URL: {top['url'][:88]}")

        if len(comps) > 1:
            print()
            print("  比較可能だった競合（単価の安い順）:")
            for c in comps[:8]:
                mark = "🔴" if c["unit_price"] < result["self_unit"] else "🟢"
                print(f"    {mark} {c['unit_price']:5.1f}円/本  ¥{c['price']:>6,}  {c['count']}本"
                      f"  {c['shop'][:20]}")
    else:
        print("  比較可能な競合が見つかりませんでした。")
        print("  → キーワードや価格帯の設定を見直す必要があります。")

    if verbose and result["excluded"]:
        print()
        print(f"  【除外された商品 {len(result['excluded'])}件】（調整の参考用）")
        for it, reason in result["excluded"][:15]:
            print(f"    ¥{it['itemPrice']:>6,}  {reason}")
            print(f"           {it.get('itemName','')[:56]}")


def build_app_json(items, results):
    """既存アプリの「調査結果のJSONを貼り付け」欄に貼れる形式で出力。"""
    now = datetime.now(JST).isoformat(timespec="seconds")
    out = {"checkedAt": now, "results": []}
    for item, r in zip(items, results):
        entry = {
            "keyword": item["keyword"],
            "mall": "rakuten",
            "status": "ok" if r["status"] == "ok" else "unavailable",
            "note": "" if r["status"] == "ok" else r.get("message", ""),
            "competitors": [],
        }
        for rank, c in enumerate(r.get("competitors", [])[:10], 1):
            entry["competitors"].append({
                "rank": rank,
                "isAd": False,
                "name": c["name"],
                "volume": f"{item['unit_size']}×{c['count']}本",
                "price": c["price"],
                "shop": c["shop"],
                "url": c["url"],
                "shipping": "unknown",
                "coupon": "",
                "points": "",
            })
        out["results"].append(entry)
    return out


def main():
    ap = argparse.ArgumentParser(description="楽天市場の競合価格をチェックする")
    ap.add_argument("--json", action="store_true", help="アプリ貼り付け用のJSONを出力")
    ap.add_argument("--verbose", action="store_true", help="除外された商品も表示")
    args = ap.parse_args()

    app_id, access_key = load_credentials()
    items = load_items()

    results = []
    for i, item in enumerate(items):
        if i:
            time.sleep(2)      # 登録QPS=1に合わせて間隔を空ける
        results.append(evaluate(item, app_id, access_key, args.verbose))

    if args.json:
        print(json.dumps(build_app_json(items, results), ensure_ascii=False, indent=2))
        return

    print()
    print(f"楽天市場 競合価格チェック  {datetime.now(JST).strftime('%Y-%m-%d %H:%M')}")
    print()
    for item, r in zip(items, results):
        print_report(item, r, args.verbose)
        print()

    alerts = [i for i, r in zip(items, results) if r.get("level") == "alert"]
    print("=" * 74)
    if alerts:
        print(f"🔴 対応が必要: {len(alerts)}件")
        for it in alerts:
            print(f"   - {it['name']}")
    else:
        print("🟢 競合に価格で負けている商品はありません。")


if __name__ == "__main__":
    main()
