# =============================================================
# analysis.py — 集計と検索のロジック（v0.1）
# w1 の search.py を元にして、集計の関数を2つ足したもの
# =============================================================


def total(records):
    """売上の合計を出す（売上 ＝ 販売数 × 売価）"""
    sales = 0

    for r in records:              # 1件ずつ足していく
        sales += r["qty"] * r["price"]

    return sales


def sum_by(records, key):
    """key（store / dept / product / date）ごとに売上を合計する

    例： sum_by(records, "store") → {"本店": 279990, "駅前店": 217418, ...}
    """
    result = {}

    for r in records:
        name = r[key]              # まとめる単位（店舗名や部門名）

        # まだ出てきていない名前なら 0 から始める
        if name not in result:
            result[name] = 0

        result[name] += r["qty"] * r["price"]

    return result


def search_records(query, records):
    """商品名・部門・店舗名でしぼりこむ（部分一致）

    ※ w1 の search_pages() とほぼ同じ。探す場所を売上データに変えただけ。
    """
    if not query.strip():
        return []

    results = []
    query_lower = query.lower()

    for r in records:
        # 検索したい項目を1つの文字列にまとめる
        search_text = r["product"] + " " + r["dept"] + " " + r["store"]

        if query_lower in search_text.lower():
            results.append(r)

    return results
