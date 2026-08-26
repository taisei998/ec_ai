"""
商品名から内容量（本数・重量）を推定し、単価換算するための共通ロジック。

楽天・Amazonの両方のレポートスクリプトから使う。
実データ検証（楽天）で判明した誤読パターンへの対策を含むため、
モールごとに複製せずここに集約している。
"""

import re


def normalize(s):
    return (s or "").replace("×", "x").replace("＊", "x").replace("*", "x")


def strip_max_phrases(s):
    """
    「最大約10kg」「上限20本」のような、実際の内容量ではなく上限を示す表現を消す。
    これを内容量として読むと単価が実際より安く見え、誤解を招くため。
    （実例：「河内晩柑 1.5kg ... 最大約 10kg」を10kgと読み 222円/kg と誤表示していた）

    「6本／10本入り」のような選択式はここでは消さない。
    自社の内容量に一致する選択肢を後段で選べるほうが実用的なため。
    """
    return re.sub(r"(?:最大|最大で|上限)\s*(?:約)?\s*\d+(?:\.\d+)?\s*(?:kg|Kg|KG|キロ|g|G|本|個|袋|パック)",
                  " ", s)


def within_range(value, hint):
    """自社の内容量から大きく外れていないか（0.5〜2倍を目安とする）。"""
    if not hint:
        return True
    return hint * 0.5 <= value <= hint * 2.0


def parse_unit(price, texts, mode, count_hint):
    """
    参考用の単価を求める。
    price: 商品価格
    texts: 商品名・キャッチコピー等の文字列リスト（優先順）
    mode: "count" なら「円/個」、"weight" なら「円/kg」
    count_hint: 自社の内容量（本数 or kg）。読み取った値がここから
                大きく外れる場合は比較対象として意味がないため除外する。

    戻り値: (単価, ラベル) または (None, None)（読めない・信頼できない場合）
    """
    for raw in texts:
        s = normalize(raw)
        if not s:
            continue
        s = strip_max_phrases(s)

        if mode == "weight":
            cands = []
            for m in re.finditer(r"(?:約)?\s*(\d+(?:\.\d+)?)\s*(?:kg|Kg|KG|キロ)", s):
                kg = float(m.group(1))
                if 0.3 <= kg <= 30:
                    cands.append(kg)
            for m in re.finditer(r"(?:約)?\s*(\d{3,5})\s*g\b", s):
                kg = int(m.group(1)) / 1000
                if 0.3 <= kg <= 30:
                    cands.append(kg)
            if not cands:
                continue
            ambiguous = len(set(cands)) > 1
            kg = min(cands, key=lambda v: abs(v - count_hint)) if count_hint else min(cands)
            if not within_range(kg, count_hint):
                return None, None
            return price / kg, f"{kg}kg" + ("?" if ambiguous else "")

        cands = []
        for pat in (r"\d{3,4}\s*(?:ml|mL|ML|g|G)\s*x?\s*(\d{1,3})\s*(?:本|個|袋|パック)",
                    r"(?:計|合計)\s*(\d{1,3})\s*(?:本|個|袋|パック)",
                    r"(\d{1,3})\s*(?:本|個|袋|パック)入",
                    r"x\s*(\d{1,3})\s*(?:本|個|袋|パック)",
                    r"(?<![\d.])(\d{1,2})\s*本(?![入り]*パック)"):
            for m in re.finditer(pat, s):
                n = int(m.group(1))
                if 1 <= n <= 200:
                    cands.append(n)
        if not cands:
            continue

        ambiguous = len(set(cands)) > 1
        n = min(cands, key=lambda v: abs(v - count_hint)) if count_hint else min(cands)
        if not within_range(n, count_hint):
            return None, None
        label = f"{n}個" + ("?" if ambiguous else "")
        return price / n, label
    return None, None
