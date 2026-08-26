# -*- coding: utf-8 -*-
"""
毎朝の価格レポート（楽天・Amazon）をまとめて実行する。

タスクスケジューラから無人で実行される想定なので、
- 片方のモールが失敗しても、もう片方は実行を続ける
- 結果は必ずファイルに保存する（画面を見ていなくても後から確認できる）
- 実行の成否をログファイルに記録する（無言で失敗しないようにする）
という3点を重視している。

出力先（すべて _ローカル専用（共有しない）/ 配下）:
    価格レポート/最新.json    ← アプリに貼り付ける用（毎回上書き）
    価格レポート/最新.txt     ← 人が読む用（毎回上書き）
    価格レポート/履歴/YYYY-MM-DD.json  ← 日付ごとの記録
    実行ログ.txt              ← 実行のたびに追記（成功/失敗の記録）

使い方:
    python daily_price_check.py
"""

import io
import sys
import json
import time
import pathlib
import traceback
import contextlib
from datetime import datetime, timezone, timedelta

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rakuten_price_check as rk
import amazon_price_check as az

JST = timezone(timedelta(hours=9))
ROOT = HERE.parent
PRIVATE = ROOT / "_ローカル専用（共有しない）"
REPORT_DIR = PRIVATE / "価格レポート"
HISTORY_DIR = REPORT_DIR / "履歴"
LOG_FILE = PRIVATE / "実行ログ.txt"


def log(msg):
    line = "[" + datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S") + "] " + msg
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_mall(label, load_items_fn, run_fn):
    """
    1モール分を実行する。失敗してもここで止めず、呼び出し元に
    (app_json, report_text, error) を返す。
    """
    try:
        items = load_items_fn()
        if not items:
            log(label + ": 監視商品が0件のためスキップ")
            return None, None, None

        app_json, results = run_fn(items)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for item, res in zip(items, results):
                if label == "楽天":
                    rk.print_report(item, res)
                else:
                    az.print_report(item, res)
        report_text = buf.getvalue()

        n_alert_free = sum(1 for r in results if r.get("error"))
        log(label + ": " + str(len(items)) + "件処理（うち取得失敗 " + str(n_alert_free) + "件）")
        return app_json, report_text, None

    except Exception as e:
        err = type(e).__name__ + ": " + str(e)
        log(label + ": 失敗 - " + err)
        log(traceback.format_exc().strip().splitlines()[-1] if traceback.format_exc() else "")
        return None, None, err


def run_rakuten(items):
    app_id, access_key = rk.load_credentials()
    results = []
    for i, item in enumerate(items):
        if i:
            time.sleep(2)
        results.append(rk.collect(item, app_id, access_key))
    return rk.build_app_json(items, results), results


def run_amazon(items):
    client_id, client_secret, refresh_token = az.load_credentials()
    access_token = az.get_access_token(client_id, client_secret, refresh_token)
    results = []
    for i, item in enumerate(items):
        if i:
            time.sleep(1)
        results.append(az.collect(item, access_token))
    return az.build_app_json(items, results), results


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    log("=== 毎朝の価格レポート 開始 ===")
    now = datetime.now(JST).isoformat(timespec="seconds")

    combined_results = []
    report_sections = []
    errors = []

    rk_json, rk_text, rk_err = run_mall("楽天", rk.load_items, run_rakuten)
    if rk_json:
        combined_results.extend(rk_json["results"])
    if rk_text:
        report_sections.append("【楽天市場】\n" + rk_text)
    if rk_err:
        errors.append("楽天: " + rk_err)

    az_json, az_text, az_err = run_mall("Amazon", az.load_items, run_amazon)
    if az_json:
        combined_results.extend(az_json["results"])
    if az_text:
        report_sections.append("【Amazon】\n" + az_text)
    if az_err:
        errors.append("Amazon: " + az_err)

    combined = {"checkedAt": now, "results": combined_results}
    combined_str = json.dumps(combined, ensure_ascii=False, indent=2)

    today = datetime.now(JST).strftime("%Y-%m-%d")
    (HISTORY_DIR / (today + ".json")).write_text(combined_str, encoding="utf-8")
    (REPORT_DIR / "最新.json").write_text(combined_str, encoding="utf-8")

    header = (
        "毎朝の価格レポート   " + datetime.now(JST).strftime("%Y-%m-%d %H:%M") + "\n"
        "※このレポートは価格の一覧です。自動判定は行っていません。\n"
    )
    if errors:
        header += "\n⚠ 一部取得に失敗しました:\n" + "\n".join("  - " + e for e in errors) + "\n"
    footer = (
        "\n次にやること：\n"
        "  1. このファイルをアプリの「価格アラート」→「調査結果のJSONを貼り付け」に貼る\n"
        "     （貼るのは 最新.json の中身です）\n"
        "  2. 気になる価格があれば、レポート内のURLを開いて実物を確認する\n"
    )
    full_text = header + "\n" + "\n\n".join(report_sections) + footer
    (REPORT_DIR / "最新.txt").write_text(full_text, encoding="utf-8")

    if errors:
        log("=== 完了（一部失敗あり）：" + str(len(combined_results)) + "件保存 ===")
    else:
        log("=== 完了：" + str(len(combined_results)) + "件保存 ===")


if __name__ == "__main__":
    main()
