# -*- coding: utf-8 -*-
"""暫定ダミーデータ生成（フロントエンド動作確認用）

【チームへの注意】
このファイルは「app.py を今すぐ動かすため」の仮のデータ生成器です。
藤井さん担当の本番ダミーデータ（data/supermarket.db）ができたら、
lib/db.py が自動的にそちらを優先して読むので、このファイルは不要になります。
テーブル名・列名は ER図（要件定義書 5.1.1）に合わせてあります。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20260930
START_DATE = "2024-10-01"
END_DATE = "2026-09-30"

# 48SKUは実店舗の品揃え（数千SKU）の「代表商品」という位置づけ。
# 想定企業（年商約100億円・8店舗）の規模に合うよう販売数を底上げする係数。
QTY_SCALE = 14.0
CUSTOMER_BASE = 1550  # 1店舗あたりの1日平均客数の基準値

# store_id, 店舗名, 地域, 売場面積, 開店年, 規模係数
STORES = [
    (1, "本店", "中央区", 1450.0, 1988, 1.40),
    (2, "駅前店", "中央区", 980.0, 1996, 1.15),
    (3, "北町店", "北区", 1100.0, 2003, 1.05),
    (4, "南台店", "南区", 890.0, 2008, 0.95),
    (5, "西原店", "西区", 760.0, 2012, 0.85),
    (6, "東山店", "東区", 1020.0, 2015, 1.00),
    (7, "港町店", "港区", 700.0, 2018, 0.80),
    (8, "みどりが丘店", "緑区", 1180.0, 2021, 1.10),
]

DEPARTMENTS = [
    (1, "青果"),
    (2, "精肉"),
    (3, "鮮魚"),
    (4, "惣菜"),
    (5, "日配"),
    (6, "グロサリー"),
]

# dept_id: (商品名, 読み仮名, カテゴリ, 標準売価, 標準原価, 1日あたり基準販売数)
PRODUCTS = {
    1: [
        ("キャベツ", "きゃべつ", "葉物", 198, 128, 22),
        ("レタス", "れたす", "葉物", 178, 118, 16),
        ("トマト", "とまと", "果菜", 298, 195, 18),
        ("きゅうり3本", "きゅうり", "果菜", 158, 100, 20),
        ("バナナ", "ばなな", "果物", 168, 105, 30),
        ("りんご", "りんご", "果物", 248, 165, 14),
        ("みかん", "みかん", "果物", 398, 262, 12),
        ("じゃがいも", "じゃがいも", "根菜", 228, 148, 15),
    ],
    2: [
        ("国産牛切り落とし", "こくさんぎゅうきりおとし", "牛肉", 798, 580, 8),
        ("豚バラスライス", "ぶたばらすらいす", "豚肉", 398, 275, 16),
        ("豚ロース", "ぶたろーす", "豚肉", 448, 310, 11),
        ("鶏もも肉", "とりももにく", "鶏肉", 328, 218, 20),
        ("鶏むね肉", "とりむねにく", "鶏肉", 198, 128, 18),
        ("合挽ミンチ", "あいびきみんち", "挽肉", 358, 248, 13),
        ("ベーコン", "べーこん", "加工肉", 298, 198, 9),
        ("ウインナー", "ういんなー", "加工肉", 268, 175, 12),
    ],
    3: [
        ("刺身盛合せ", "さしみもりあわせ", "刺身", 980, 720, 7),
        ("まぐろ柵", "まぐろさく", "刺身", 780, 570, 6),
        ("サーモン刺身", "さーもんさしみ", "刺身", 680, 490, 9),
        ("ぶり切身", "ぶりきりみ", "切身", 498, 355, 8),
        ("鮭切身", "さけきりみ", "切身", 398, 278, 12),
        ("さんま", "さんま", "丸魚", 248, 168, 7),
        ("あじ開き", "あじひらき", "干物", 298, 198, 8),
        ("ほたて", "ほたて", "貝類", 598, 440, 5),
    ],
    4: [
        ("唐揚げ", "からあげ", "揚物", 398, 235, 18),
        ("コロッケ", "ころっけ", "揚物", 98, 55, 26),
        ("幕の内弁当", "まくのうちべんとう", "弁当", 598, 380, 14),
        ("のり弁当", "のりべんとう", "弁当", 398, 250, 16),
        ("助六寿司", "すけろくずし", "寿司", 498, 320, 10),
        ("ポテトサラダ", "ぽてとさらだ", "サラダ", 258, 150, 12),
        ("焼き鳥5本", "やきとり", "焼物", 448, 275, 11),
        ("ハンバーグ弁当", "はんばーぐべんとう", "弁当", 548, 350, 9),
    ],
    5: [
        ("牛乳1L", "ぎゅうにゅう", "乳製品", 218, 160, 34),
        ("ヨーグルト", "よーぐると", "乳製品", 158, 108, 24),
        ("卵10個", "たまご", "卵", 268, 205, 30),
        ("木綿豆腐", "もめんとうふ", "豆腐", 88, 55, 26),
        ("納豆3P", "なっとう", "納豆", 108, 68, 22),
        ("食パン6枚", "しょくぱん", "パン", 178, 118, 20),
        ("プリン3個", "ぷりん", "デザート", 158, 100, 14),
        ("スライスチーズ", "すらいすちーず", "乳製品", 248, 172, 12),
    ],
    6: [
        ("米5kg", "こめ", "米", 2480, 2050, 4),
        ("醤油1L", "しょうゆ", "調味料", 398, 285, 6),
        ("サラダ油", "さらだあぶら", "調味料", 458, 340, 5),
        ("カップ麺", "かっぷめん", "即席麺", 158, 112, 20),
        ("スナック菓子", "すなっくがし", "菓子", 128, 88, 24),
        ("緑茶2L", "りょくちゃ", "飲料", 158, 108, 18),
        ("炭酸水", "たんさんすい", "飲料", 98, 62, 22),
        ("ビール6缶", "びーる", "酒類", 1180, 980, 7),
    ],
}

SUPPLIERS = [
    (1, "地場青果卸センター"),
    (2, "北陸ミートパック"),
    (3, "日本海水産"),
    (4, "デリカ食品工業"),
    (5, "中央食品商事"),
    (6, "全国酒類卸"),
]
DEPT_SUPPLIER = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6}

COMPETITORS = [
    (1, "ドラッグ・スギヤマ 西原店", "ドラッグストア", 5),
    (2, "スーパーやまびこ 本町店", "スーパー", 1),
    (3, "ドラッグ・コスモ 南台店", "ドラッグストア", 4),
]

# 季節性（月ごとの係数）：部門ごとの売れ方の違いを表現する
SEASON = {
    1: [0.95, 0.95, 1.00, 1.05, 1.08, 1.10, 1.12, 1.12, 1.05, 1.00, 0.98, 1.00],  # 青果
    2: [1.05, 1.00, 1.00, 1.00, 1.02, 1.00, 1.02, 1.05, 1.02, 1.03, 1.05, 1.15],  # 精肉
    3: [1.15, 1.05, 1.00, 0.98, 0.95, 0.92, 0.92, 0.95, 1.00, 1.05, 1.12, 1.25],  # 鮮魚
    4: [1.02, 1.00, 1.00, 1.02, 1.03, 1.03, 1.05, 1.05, 1.02, 1.02, 1.03, 1.08],  # 惣菜
    5: [1.00, 1.00, 1.00, 1.00, 1.02, 1.03, 1.05, 1.05, 1.00, 1.00, 1.00, 1.02],  # 日配
    6: [1.05, 1.00, 0.98, 0.98, 1.00, 1.05, 1.10, 1.08, 1.00, 0.98, 1.02, 1.12],  # グロサリー
}
WEEKDAY = np.array([0.88, 0.84, 0.90, 0.95, 1.15, 1.45, 1.28])  # 月〜日
WASTE_DEPTS = {1, 3, 4}  # 廃棄が出やすい部門（青果・鮮魚・惣菜）


def _build_masters() -> dict[str, pd.DataFrame]:
    stores = pd.DataFrame(
        [(s[0], s[1], s[2], s[3], s[4]) for s in STORES],
        columns=["store_id", "store_name", "area", "floor_m2", "opened_year"],
    )
    departments = pd.DataFrame(DEPARTMENTS, columns=["dept_id", "dept_name"])
    suppliers = pd.DataFrame(SUPPLIERS, columns=["supplier_id", "supplier_name"])

    rows = []
    pid = 1
    for dept_id, items in PRODUCTS.items():
        for name, kana, category, price, cost, base_qty in items:
            rows.append(
                (pid, name, kana, dept_id, category, float(price), float(cost),
                 f"49{pid:011d}", float(base_qty), DEPT_SUPPLIER[dept_id])
            )
            pid += 1
    products = pd.DataFrame(
        rows,
        columns=["product_id", "product_name", "kana", "dept_id", "category",
                 "std_price", "std_cost", "jan_code", "_base_qty", "_supplier_id"],
    )
    competitors = pd.DataFrame(
        COMPETITORS,
        columns=["competitor_id", "competitor_name", "business_type", "near_store_id"],
    )
    return {
        "stores": stores,
        "departments": departments,
        "suppliers": suppliers,
        "products": products,
        "competitors": competitors,
    }


def _build_transactions(masters: dict[str, pd.DataFrame], rng: np.random.Generator):
    dates = pd.date_range(START_DATE, END_DATE, freq="D")
    products = masters["products"]
    n_d, n_s, n_p = len(dates), len(STORES), len(products)

    # --- 係数の準備（ブロードキャストで一気に計算する） -------------------
    month_idx = dates.month.to_numpy() - 1
    dow = dates.dayofweek.to_numpy()
    day_f = WEEKDAY[dow]                                   # (n_d,)
    store_f = np.array([s[5] for s in STORES])             # (n_s,)
    base_q = products["_base_qty"].to_numpy()              # (n_p,)
    dept_of_p = products["dept_id"].to_numpy()
    season_f = np.array([[SEASON[d][m] for d in dept_of_p] for m in month_idx])  # (n_d, n_p)

    # 全社のゆるやかな減少トレンド（人口減・競争激化）
    trend = np.linspace(1.02, 0.96, n_d)[:, None, None]

    # 部門ごとの伸び／落ち（2年間の増減率）＋商品ごとのばらつき。
    # これがないと全商品が同じ動きになり、「伸びている商品／落ちている商品」が見えなくなる。
    dept_growth = {1: 0.00, 2: 0.03, 3: -0.10, 4: 0.12, 5: -0.05, 6: -0.06}
    growth = np.array([dept_growth[d] for d in dept_of_p]) + rng.normal(0, 0.09, n_p)
    prod_trend = np.linspace(np.ones(n_p), 1 + growth, n_d)      # (n_d, n_p)

    factor = (
        QTY_SCALE
        * base_q[None, None, :]
        * store_f[None, :, None]
        * day_f[:, None, None]
        * season_f[:, None, :]
        * prod_trend[:, None, :]
        * trend
    )

    # シナリオ1：西原店（store_id=5）の隣にドラッグストアが開店（2026-04以降、日配・グロサリーが落ちる）
    after = np.asarray(dates >= "2026-04-01")
    hit_dept = np.isin(dept_of_p, [5, 6])
    factor[np.ix_(after, [4], np.where(hit_dept)[0])] *= 0.78

    # シナリオ2：港町店（store_id=7）の惣菜は作りすぎ傾向（あとで廃棄が多く出る）
    promo = rng.random((n_d, 1, n_p)) < 0.06          # 特売（日 × 商品）
    promo = np.broadcast_to(promo, (n_d, n_s, n_p))
    lam = factor * np.where(promo, 1.9, 1.0) * rng.normal(1.0, 0.12, (n_d, n_s, n_p)).clip(0.4, 1.8)
    qty = rng.poisson(lam.clip(0.05, None)).astype(np.int32)

    d_idx, s_idx, p_idx = np.nonzero(qty)
    q = qty[d_idx, s_idx, p_idx]
    pr = promo[d_idx, s_idx, p_idx]
    price = products["std_price"].to_numpy()[p_idx] * np.where(pr, 0.82, 1.0)
    cost = products["std_cost"].to_numpy()[p_idx]

    sales = pd.DataFrame({
        "sales_date": dates.to_numpy()[d_idx],
        "store_id": np.array([s[0] for s in STORES])[s_idx],
        "product_id": products["product_id"].to_numpy()[p_idx],
        "qty": q,
        "amount": np.round(price * q, 0),
        "gross_profit": np.round((price - cost) * q, 0),
        "is_promo": pr.astype(np.int8),
    })
    sales["sales_date"] = sales["sales_date"].astype("datetime64[ns]")

    # --- 在庫：だいたい2〜4日分を持っている ------------------------------
    # 直近90日は全商品ぶん、それ以前は行数を抑えるため一部日付のみ保持する。
    recent_from = (pd.Timestamp(END_DATE) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    is_recent = sales["sales_date"].dt.strftime("%Y-%m-%d").to_numpy() >= recent_from
    inv_keep = is_recent | (rng.random(len(sales)) < 0.25)
    # 在庫の持ち方は部門で大きく違う（惣菜・鮮魚は当日売り切り、グロサリーは数日分）
    stock_days = {1: 1.2, 2: 1.5, 3: 0.8, 4: 0.5, 5: 1.8, 6: 5.0}
    dept_by_pid0 = dict(zip(products["product_id"], products["dept_id"]))
    keep_pid = sales.loc[inv_keep, "product_id"]
    days_arr = keep_pid.map(dept_by_pid0).map(stock_days).to_numpy()
    inventory = pd.DataFrame({
        "inv_date": sales.loc[inv_keep, "sales_date"].to_numpy(),
        "store_id": sales.loc[inv_keep, "store_id"].to_numpy(),
        "product_id": keep_pid.to_numpy(),
        "stock_qty": rng.poisson(
            sales.loc[inv_keep, "qty"].to_numpy() * days_arr + 1).astype(np.int32),
    })

    # --- 廃棄：生鮮・惣菜のみ。港町店の惣菜は多め ------------------------
    dept_by_pid = dict(zip(products["product_id"], products["dept_id"]))
    s_dept = sales["product_id"].map(dept_by_pid).to_numpy()
    is_waste_dept = np.isin(s_dept, list(WASTE_DEPTS))
    rate = np.where(is_waste_dept, 0.035, 0.0)
    rate = np.where((sales["store_id"].to_numpy() == 7) & (s_dept == 4), 0.11, rate)
    w_qty = rng.poisson(sales["qty"].to_numpy() * rate)
    w_mask = w_qty > 0
    cost_by_pid = dict(zip(products["product_id"], products["std_cost"]))
    waste = pd.DataFrame({
        "waste_date": sales.loc[w_mask, "sales_date"].to_numpy(),
        "store_id": sales.loc[w_mask, "store_id"].to_numpy(),
        "product_id": sales.loc[w_mask, "product_id"].to_numpy(),
        "waste_qty": w_qty[w_mask].astype(np.int32),
    })
    waste["waste_amount"] = np.round(
        waste["waste_qty"] * waste["product_id"].map(cost_by_pid).to_numpy(), 0
    )
    waste["waste_date"] = waste["waste_date"].astype("datetime64[ns]")

    # --- 欠品：特売日と売れ筋で起こりやすい ------------------------------
    so_p = 0.012 + 0.03 * sales["is_promo"].to_numpy()
    so_mask = rng.random(len(sales)) < so_p
    slots = np.array(["午前", "午後", "夕方", "夜"])
    stockouts = pd.DataFrame({
        "stockout_date": sales.loc[so_mask, "sales_date"].to_numpy(),
        "time_slot": rng.choice(slots, so_mask.sum(), p=[0.15, 0.25, 0.40, 0.20]),
        "store_id": sales.loc[so_mask, "store_id"].to_numpy(),
        "product_id": sales.loc[so_mask, "product_id"].to_numpy(),
    })
    stockouts["stockout_date"] = stockouts["stockout_date"].astype("datetime64[ns]")

    # --- 仕入：週1回（月曜）にまとめて発注する運用とする ------------------
    wk = sales.copy()
    wk["purchase_date"] = wk["sales_date"] - pd.to_timedelta(wk["sales_date"].dt.dayofweek, unit="D")
    purchases = (
        wk.groupby(["purchase_date", "store_id", "product_id"], as_index=False)["qty"].sum()
        .rename(columns={"qty": "purchase_qty"})
    )
    purchases["purchase_qty"] = (purchases["purchase_qty"] * rng.normal(1.05, 0.08, len(purchases))).round().astype(int).clip(1, None)
    months_from_start = (
        (purchases["purchase_date"].dt.year - pd.Timestamp(START_DATE).year) * 12
        + purchases["purchase_date"].dt.month - pd.Timestamp(START_DATE).month
    )
    inflation = 1.0 + months_from_start * 0.0025          # 仕入原価のゆるやかな上昇
    purchases["unit_cost"] = np.round(
        purchases["product_id"].map(cost_by_pid).to_numpy()
        * inflation.to_numpy()
        * rng.normal(1.0, 0.03, len(purchases)), 1
    )
    purchases["supplier_id"] = purchases["product_id"].map(
        dict(zip(products["product_id"], products["_supplier_id"]))
    )

    # --- 客数 -------------------------------------------------------------
    cust = []
    for (sid, _, _, _, _, f) in STORES:
        base = CUSTOMER_BASE * f
        n = base * day_f * np.linspace(1.02, 0.95, n_d) * rng.normal(1.0, 0.07, n_d)
        cust.append(pd.DataFrame({
            "visit_date": dates,
            "store_id": sid,
            "customer_count": n.round().astype(int),
            "member_count": (n * rng.uniform(0.55, 0.68, n_d)).round().astype(int),
        }))
    customers_daily = pd.concat(cust, ignore_index=True)

    # --- 予算：実績をもとに「ほぼ達成する前提の計画値」を置く --------------
    s2 = sales.copy()
    s2["dept_id"] = s2["product_id"].map(dept_by_pid)
    s2["year_month"] = s2["sales_date"].dt.strftime("%Y-%m")
    budgets = (
        s2.groupby(["year_month", "store_id", "dept_id"], as_index=False)
        .agg(sales_budget=("amount", "sum"), gp_budget=("gross_profit", "sum"))
    )
    noise = rng.normal(1.01, 0.05, len(budgets)).clip(0.9, 1.15)
    budgets["sales_budget"] = (budgets["sales_budget"] * noise).round()
    budgets["gp_budget"] = (budgets["gp_budget"] * noise).round()

    # --- 競合価格（クローラーで取る想定のテーブル） ------------------------
    weeks = pd.date_range(START_DATE, END_DATE, freq="W-MON")
    target = products.sample(24, random_state=7)
    rec = []
    pid_seq = 1
    for _, p in target.iterrows():
        for c in COMPETITORS:
            # ドラッグストアは日配・グロサリーを安くしてくる。
            # それ以外は競合の方が高い商品・安い商品が混ざるようにする。
            if c[2] == "ドラッグストア" and p["dept_id"] in (5, 6):
                base_ratio = rng.uniform(0.82, 0.90)
            else:
                base_ratio = rng.uniform(0.94, 1.08)
            for w in weeks:
                rec.append((
                    pid_seq, w.strftime("%Y-%m-%d 06:00:00"), c[0], p["product_name"],
                    int(p["product_id"]),
                    float(np.round(p["std_price"] * base_ratio * rng.normal(1.0, 0.05), 0)),
                    f"https://example.com/chirashi/{c[0]}/{w:%Y%m%d}",
                ))
                pid_seq += 1
    competitor_prices = pd.DataFrame(
        rec,
        columns=["price_id", "fetched_at", "competitor_id", "raw_name", "product_id", "price", "source_url"],
    )

    # --- 天気（発注量の補正に使う想定。ER図には未記載＝要確認） ------------
    tmean = 15 + 11 * np.sin((dates.dayofyear.to_numpy() - 110) / 365 * 2 * np.pi)
    weather_daily = pd.DataFrame({
        "weather_date": dates,
        "area": "中央区",
        "weather": rng.choice(["晴", "曇", "雨", "雪"], n_d, p=[0.45, 0.28, 0.22, 0.05]),
        "temp_avg": np.round(tmean + rng.normal(0, 2.2, n_d), 1),
    })

    return {
        "sales": sales,
        "inventory": inventory,
        "waste": waste,
        "stockouts": stockouts,
        "purchases": purchases,
        "customers_daily": customers_daily,
        "budgets": budgets,
        "competitor_prices": competitor_prices,
        "weather_daily": weather_daily,
    }


def _knowledge() -> pd.DataFrame:
    rows = [
        (1, "雨の日の惣菜は14時までに作り切る", "雨の日は夕方の客数が2割落ちる。揚物は14時以降の追加調理を止めて、15時からの値引きを早めると廃棄がほぼ出ない。",
         4, "惣菜,廃棄,天気", "", "港町店 山本", "2026-06-12", 12),
        (2, "特売バナナは入口平台で2段積み", "バナナの特売は入口の平台に2段で積むと、通常什器の1.6倍売れた。POPは黄色地に黒文字で価格だけ大きく。",
         1, "青果,売場,POP", "", "本店 佐藤", "2026-05-28", 9),
        (3, "夕方の刺身は3点盛りに組み替える", "16時時点で単品の刺身が残ったら、値引きより先に3点盛りへ組み替える方が粗利が残る。",
         3, "鮮魚,値引き,粗利", "", "駅前店 鈴木", "2026-07-03", 15),
        (4, "牛乳の欠品は金曜夕方に集中", "金曜の夕方に牛乳1Lが欠品しやすい。木曜の発注を1.3倍にしたら欠品がゼロになった。",
         5, "日配,欠品,発注", "", "北町店 田中", "2026-08-19", 7),
        (5, "ドラッグストア対抗は日配の底値追随をやめる", "西原店の日配は価格で勝てない。地場の豆腐・納豆を前面に出したら粗利率が1.2pt改善した。",
         5, "日配,競合,価格", "", "西原店 高橋", "2026-09-02", 18),
        (6, "新人向け：発注端末の入力手順", "発注端末は前年同週の実績を見てから入力する。特売週は前年の特売週と比べること。",
         6, "新人教育,発注", "", "本店 伊藤", "2026-04-10", 5),
    ]
    return pd.DataFrame(
        rows,
        columns=["knowledge_id", "title", "body", "dept_id", "tags", "photo_path",
                 "author", "created_at", "helpful_count"],
    )


INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_sales ON sales(sales_date, store_id, product_id)",
    "CREATE INDEX IF NOT EXISTS ix_sales_p ON sales(product_id)",
    "CREATE INDEX IF NOT EXISTS ix_inv ON inventory(inv_date, store_id, product_id)",
    "CREATE INDEX IF NOT EXISTS ix_waste ON waste(waste_date, store_id, product_id)",
    "CREATE INDEX IF NOT EXISTS ix_so ON stockouts(stockout_date, store_id, product_id)",
    "CREATE INDEX IF NOT EXISTS ix_pur ON purchases(purchase_date, store_id, product_id)",
    "CREATE INDEX IF NOT EXISTS ix_bud ON budgets(year_month, store_id, dept_id)",
    "CREATE INDEX IF NOT EXISTS ix_cust ON customers_daily(visit_date, store_id)",
    "CREATE INDEX IF NOT EXISTS ix_cp ON competitor_prices(product_id, competitor_id)",
]


def generate(db_path: str | Path, progress=None) -> Path:
    """ダミーデータを生成して SQLite ファイルに書き出す。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    rng = np.random.default_rng(SEED)
    if progress:
        progress("マスタを作成中…", 0.1)
    masters = _build_masters()
    if progress:
        progress("販売・在庫・廃棄などのトランザクションを生成中…", 0.3)
    trans = _build_transactions(masters, rng)
    if progress:
        progress("データベースに書き込み中…", 0.75)

    tables: dict[str, pd.DataFrame] = {}
    tables.update({k: v for k, v in masters.items()})
    tables["products"] = tables["products"].drop(columns=["_base_qty", "_supplier_id"])
    tables.update(trans)
    tables["knowledge"] = _knowledge()

    con = sqlite3.connect(db_path)
    try:
        for name, df in tables.items():
            out = df.copy()
            for col in out.columns:
                if pd.api.types.is_datetime64_any_dtype(out[col]):
                    out[col] = out[col].dt.strftime("%Y-%m-%d")
            out.to_sql(name, con, index=False, if_exists="replace", chunksize=20_000)
        for sql in INDEXES:
            con.execute(sql)
        con.commit()
    finally:
        con.close()
    if progress:
        progress("完了", 1.0)
    return db_path


if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / "data" / "supermarket_dummy.db"
    generate(path, progress=lambda m, p: print(f"[{p:>4.0%}] {m}"))
    print("生成しました:", path)
