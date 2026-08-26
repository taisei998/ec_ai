# -*- coding: utf-8 -*-
"""
価格レポートの日次差分を検出する。

daily_price_check.py が生成した「価格レポート/履歴/YYYY-MM-DD.json」を、
直近の一つ前のレポートと比較し、「同じ商品(URL)が値下げした」「新しく
見つかった競合」だけを拾い出す。判定（🔴🟢等）はしない、という
これまでの方針を守ったまま、「前回と比べて何が変わったか」という
事実だけを報告する。

■ なぜ「今日の最安値」同士を比べないのか
検索結果はキーワード検索である以上、日によって（同じ日の別実行でも）
取れる商品の顔ぶれが変わりうる（実際に同日で17件→20件になった例がある）。
「今日の最安値」を単純に前回と比べると、この揺らぎのせいで実際には
値下げしていないのに通知が飛ぶ恐れがある。
そのため「同じURLの商品」を軸に、その商品自身の値段が下がったかどうかで
比較する。新規に見つかった競合は、値下がりとは別に「新しい競合」として
報告する（これも判定ではなく事実の報告）。

使い方:
    python compare_price_reports.py           # 人が読む形で表示
    python compare_price_reports.py --json     # JSON出力（通知の判断用）
"""

import sys
import json
import pathlib
import argparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRIVATE = ROOT / "_ローカル専用（共有しない）"
HISTORY_DIR = PRIVATE / "価格レポート" / "履歴"


def load_dated_reports():
    """履歴フォルダの日付ファイルを日付昇順で返す。[(date_str, path), ...]"""
    if not HISTORY_DIR.exists():
        return []
    files = sorted(HISTORY_DIR.glob("????-??-??.json"))
    return [(f.stem, f) for f in files]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def index_by_keyword_mall(report):
    """{(keyword, mall): {url: {price, name, shop}}} の形に変換する。"""
    out = {}
    for entry in report.get("results", []):
        if entry.get("status") != "ok":
            continue
        key = (entry.get("keyword"), entry.get("mall"))
        by_url = {}
        for c in entry.get("competitors", []):
            url = c.get("url")
            if not url:
                continue
            by_url[url] = {"price": c.get("price"), "name": c.get("name"), "shop": c.get("shop")}
        out[key] = by_url
    return out


def compare(today_report, prev_report):
    """
    戻り値: {
      "decreases": [{keyword, mall, name, shop, url, prev_price, today_price, diff}],
      "new_listings": [{keyword, mall, name, shop, url, price}],
    }
    """
    today_idx = index_by_keyword_mall(today_report)
    prev_idx = index_by_keyword_mall(prev_report)

    decreases = []
    new_listings = []

    for key, today_urls in today_idx.items():
        keyword, mall = key
        prev_urls = prev_idx.get(key, {})

        for url, info in today_urls.items():
            price = info["price"]
            if price is None:
                continue
            if url in prev_urls:
                prev_price = prev_urls[url]["price"]
                if prev_price is not None and price < prev_price:
                    decreases.append({
                        "keyword": keyword, "mall": mall,
                        "name": info["name"], "shop": info["shop"], "url": url,
                        "prev_price": prev_price, "today_price": price,
                        "diff": prev_price - price,
                    })
            else:
                new_listings.append({
                    "keyword": keyword, "mall": mall,
                    "name": info["name"], "shop": info["shop"], "url": url,
                    "price": price,
                })

    decreases.sort(key=lambda d: -d["diff"])
    return {"decreases": decreases, "new_listings": new_listings}


def main():
    ap = argparse.ArgumentParser(description="価格レポートの前回比較を行う")
    ap.add_argument("--json", action="store_true", help="JSON形式で出力")
    args = ap.parse_args()

    dated = load_dated_reports()
    if len(dated) == 0:
        result = {"status": "no_data", "message": "レポート履歴がまだありません。"}
    elif len(dated) == 1:
        result = {"status": "first_run", "message": "初回のため比較対象がありません（次回から比較します）。",
                  "latest_date": dated[-1][0]}
    else:
        today_date, today_path = dated[-1]
        prev_date, prev_path = dated[-2]
        today_report = read_json(today_path)
        prev_report = read_json(prev_path)
        diff = compare(today_report, prev_report)
        result = {
            "status": "compared",
            "today_date": today_date,
            "prev_date": prev_date,
            "decreases": diff["decreases"],
            "new_listings": diff["new_listings"],
            "has_changes": bool(diff["decreases"] or diff["new_listings"]),
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if result["status"] in ("no_data", "first_run"):
        print(result["message"])
        return

    print("比較: " + result["prev_date"] + " → " + result["today_date"])
    print()

    if not result["has_changes"]:
        print("変化なし（値下げ・新規競合とも検出されませんでした）")
        return

    if result["decreases"]:
        print("【値下げを検出】" + str(len(result["decreases"])) + "件")
        for d in result["decreases"]:
            print("  " + d["mall"] + " / " + d["keyword"])
            print("    ¥{:,} → ¥{:,}（-¥{:,}）".format(d["prev_price"], d["today_price"], d["diff"]))
            print("    " + d["name"][:50])
            print("    " + d["url"])
        print()

    if result["new_listings"]:
        print("【新しい競合を検出】" + str(len(result["new_listings"])) + "件")
        for n in result["new_listings"]:
            print("  " + n["mall"] + " / " + n["keyword"])
            print("    ¥{:,}".format(n["price"]) + "  " + n["name"][:50])
            print("    " + n["url"])
        print()


if __name__ == "__main__":
    main()
