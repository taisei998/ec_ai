# 新商品開発ナビ

EC事業部の社内ツール。新商品リサーチ・競合価格アラート・食品業界ニュースを1つにまとめたもの。

- **公開URL**: https://taisei998.github.io/ec_ai/shinshouhin-navi.html
- **ログインパスワード**: 別途参照（このファイルには書かない）
- **リポジトリ**: https://github.com/taisei998/ec_ai

---

## 🤖 Claude に相談するときは、これをそのまま貼ってください

Claude chat（claude.ai）は、このパソコンの中のファイルを直接読めません。
かわりに GitHub 経由で読めるので、**下のブロックをコピーして貼り付けてください。**

```
EC事業部の社内ツール「新商品開発ナビ」について相談します。
以下を読んで、現状の実装と設計判断を把握してから回答してください。

1. 開発の経緯・設計判断・既知の制約（最初に必ずこれを読んでください）
https://raw.githubusercontent.com/taisei998/ec_ai/main/HANDOFF.md

2. アプリ本体（単一HTMLファイル。CSS/JSすべてインライン）
https://raw.githubusercontent.com/taisei998/ec_ai/main/shinshouhin-navi.html

3. API自動化の進め方（3モール自動化ロードマップ）
https://raw.githubusercontent.com/taisei998/ec_ai/main/docs/api-automation-roadmap.md

【相談したいこと】
（ここに質問を書く）
```

> **なぜ `raw.githubusercontent.com` なのか**
> 通常の GitHub のページURLだと画面の飾りごと読み込んでしまい、
> ファイルの中身が正しく取れないことがあります。`raw.` から始まるURLは
> ファイルの中身そのものなので、Claude が確実に読めます。

---

## フォルダの中身

| 場所 | 中身 | GitHubに公開される？ |
|---|---|---|
| `shinshouhin-navi.html` | アプリ本体。これ1つで動く | ✅ される |
| `HANDOFF.md` | 開発の経緯・なぜこの実装にしたか・既知の制約 | ✅ される |
| `docs/` | API自動化の進め方などの資料 | ✅ される |
| `_ローカル専用（共有しない）/` | **APIキー・パスワードを置く場所** | ❌ されない |

### ⚠️ APIキーの置き場所について

**このリポジトリは GitHub で公開されています。**
Amazon SP-API・楽天・Yahoo! の鍵をここに書くと、世界中の誰でも読めてしまいます。

鍵は必ず `_ローカル専用（共有しない）/` フォルダの中に置いてください。
このフォルダは `.gitignore` で除外してあるので、GitHubには絶対に上がりません。

---

## 開発するとき（Claude Code 用）

- このフォルダがそのまま Git リポジトリです。`git` コマンドはここで動きます
- ローカル確認用サーバー: `python -m http.server 8765` → http://localhost:8765/shinshouhin-navi.html
- **node は未インストール**のため、`node --check` による構文チェックは使えません。
  代わりに Playwright（Python版）で実ロードし `page.on("pageerror")` を収集して検証します
- 検証手順の詳細は `HANDOFF.md` の「開発・検証の進め方」を参照

## いまの状況（2026年8月20日時点）

- アプリは公開済み・稼働中。利用者は本人のみ（本格導入前）
- 価格アラートは「依頼文をコピー → Claudeに調査依頼 → 返ってきたJSONを貼り戻す」方式
- **次にやること**: Amazon・楽天・Yahoo! の公式APIで、この貼り戻しを自動化する
  （詳細は `docs/api-automation-roadmap.md`）
