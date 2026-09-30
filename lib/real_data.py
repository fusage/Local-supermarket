# -*- coding: utf-8 -*-
"""本番DBの組み立て（データ班のデータを正として、足りない部分を補完する）

data/csv/*.csv（データ班の M_* / T_*）から、画面が使うER図のテーブルを作ります。

【そのまま使うもの】
  ・店舗（M_STORE）、商品（M_PRODUCT）の名前・売価・原価
  ・T_SALES の期間（7日分）の売上・在庫・廃棄・欠品・発注・天気
    → この期間・この商品の数字は、「ロス分析」画面（M_* / T_* を直接見る）と一致します

【補完するもの（生成データ）】
  ・それ以外の期間の売上・在庫・廃棄・欠品・仕入・天気
    （実データの最終日がある月の月末まで、さかのぼって2年ぶん。
      月の途中で切ると、月次の推移や予算比が途中の月だけ小さく見えてしまうため）
  ・データ班の商品に無い部門の代表商品（lib/sample_data.py の商品表から、重複を除いて追加）
  ・データ班のマスタに無い項目（売場面積・開店年・読み仮名・部門・JAN など）
  ・予算、客数、仕入先、競合店と競合価格、ナレッジ

補完ぶんの売れ方は、データ班の scripts/create_transactions.py と同じ前提
（部門ごとの基準販売数、気温が急に下がった日の鍋関連の特需）に、
曜日・季節・店舗規模・特売の変動を足したものです。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from lib import sample_data as sd
from lib.paths import CSV_DIR

SEED = 20260926
YEARS = 2                     # 前年比を出すために必要な年数
TARGET_SPEND = 1150           # 客数を逆算するための客単価の目安（代表商品ぶん・円）
REQUIRED = ("M_STORE", "M_PRODUCT", "M_WEATHER",
            "T_SALES", "T_ORDER", "T_INVENTORY", "T_WASTE_DISCOUNT")

# --------------------------------------------------------------------------
# データ班のマスタに無い項目の補完
# --------------------------------------------------------------------------
# store_cd: (売場面積, 開店年, 規模係数)
STORE_EXTRA = {
    "ST001": (1450.0, 1988, 1.15),
    "ST002": (1100.0, 1996, 1.00),
    "ST003": (890.0, 2003, 0.90),
    "ST004": (1180.0, 2012, 1.05),
    "ST005": (760.0, 2018, 0.90),
}
STORE_DEFAULT = (1000.0, 2000, 1.00)      # 上の表に無い店舗が増えたとき

# product_cd: (読み仮名, 部門ID, カテゴリ)
# データ班の区分「生鮮」は、画面の部門（青果・精肉・鮮魚）に商品ごとに割り当てる。
PRODUCT_EXTRA = {
    "SOZ001": ("とくせいろーすかつべんとう", 4, "弁当"),
    "SOZ002": ("てづくりとりからあげ", 4, "揚物"),
    "SOZ003": ("こくさんぶたのしょうがやきべんとう", 4, "弁当"),
    "SOZ004": ("いろどりぽてとさらだ", 4, "サラダ"),
    "NIP001": ("ほんかくよせなべつゆ", 5, "鍋つゆ"),
    "NIP002": ("のうこうぱいたんなべつゆ", 5, "鍋つゆ"),
    "NIP003": ("もっちりもめんとうふ", 5, "豆腐"),
    "NIP004": ("きぬごしひややっこようとうふ", 5, "豆腐"),
    "NIP005": ("さぬきうどん", 5, "麺"),
    "SEI001": ("こくさんぶたばらにく", 2, "豚肉"),
    "SEI002": ("わかどりももにく", 2, "鶏肉"),
    "SEI003": ("なべようかっとやさいせっと", 1, "カット野菜"),
}
CATEGORY_DEPT = {"惣菜": 4, "日配": 5, "生鮮": 2}      # 上の表に無い商品が増えたとき

# データ班の商品と中身が重なるため、補完の商品表から外すもの
DUPLICATES = {"唐揚げ", "ポテトサラダ", "木綿豆腐", "豚バラスライス", "鶏もも肉"}

# --- scripts/create_transactions.py と同じ前提 ---------------------------
BASE_DEMAND = {"惣菜": 30, "日配": 40, "生鮮": 25}      # 1店舗1日あたりの基準販売数
NABE_ITEMS = {"NIP001", "NIP002", "NIP003", "SEI001", "SEI003"}
COLD_SNAP = -3.0              # 前日との気温差がこれ以下の日は、鍋関連の需要が増える
NABE_FACTOR = 1.45
NABE_MAX_TEMP = 25.0          # 補完で足した条件：最高気温がこれ以上の日（真夏）は特需にしない
AI_ADOPTER = "ST001"          # AIの発注提案を採用する店舗（特需を取りこぼさない）
NABE_FACTOR_MISSED = 1.12     # 発注を据え置いた店舗が売れる上限（在庫ぶんしか売れない）

# --- 補完ぶんに入れてある状況（発表デモ用。lib/sample_data.py と同じ筋書き）----
DRUGSTORE_STORE = "ST002"     # 2026年4月、隣にドラッグストアが開店（日配・グロサリーが落ちる）
DRUGSTORE_FROM = "2026-04-01"
OVERPRODUCTION_STORE = "ST005"  # 惣菜を作りすぎる傾向（廃棄が多い）

# competitor_id, 競合店名, 業態, 近くの店舗（store_cd）
COMPETITORS = [
    (1, "ドラッグ・スギヤマ 堀川店", "ドラッグストア", "ST002"),
    (2, "スーパーやまびこ 桜町店", "スーパー", "ST001"),
    (3, "ドラッグ・コスモ 婦中店", "ドラッグストア", "ST004"),
]
STOCK_DAYS = {1: 1.2, 2: 1.5, 3: 0.8, 4: 0.5, 5: 1.8, 6: 5.0}   # 部門ごとの在庫の持ち方（日数）


def available() -> bool:
    """データ班のCSVがそろっているか（そろっていれば本番DBを組み立てられる）。"""
    return all((CSV_DIR / f"{t}.csv").exists() for t in REQUIRED)


def _csv(name: str) -> pd.DataFrame:
    return pd.read_csv(CSV_DIR / f"{name}.csv", encoding="utf-8-sig")


# --------------------------------------------------------------------------
# マスタ
# --------------------------------------------------------------------------
def _build_masters(src: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    ms = src["M_STORE"].reset_index(drop=True)
    extra = [STORE_EXTRA.get(cd, STORE_DEFAULT) for cd in ms["store_cd"]]
    stores = pd.DataFrame({
        "store_id": np.arange(1, len(ms) + 1),
        "store_name": ms["store_name"],
        "area": ms["address"],
        "floor_m2": [e[0] for e in extra],
        "opened_year": [e[1] for e in extra],
        "store_cd": ms["store_cd"],
        "trade_area_type": ms["trade_area_type"],
        "_factor": [e[2] for e in extra],
    })

    rows = []
    for r in src["M_PRODUCT"].itertuples(index=False):
        kana, dept_id, category = PRODUCT_EXTRA.get(
            r.product_cd, ("", CATEGORY_DEPT.get(r.category, 6), r.category))
        rows.append((r.product_name, kana, dept_id, category, float(r.sales_price),
                     float(r.cost_price), r.product_cd, int(r.shelf_life_days),
                     "データ班", float(BASE_DEMAND.get(r.category, 25))))
    real_names = set(src["M_PRODUCT"]["product_name"])
    for dept_id, items in sd.PRODUCTS.items():
        for name, kana, category, price, cost, base_qty in items:
            if name in DUPLICATES or name in real_names:
                continue
            rows.append((name, kana, dept_id, category, float(price), float(cost),
                         None, None, "補完", float(base_qty)))
    products = pd.DataFrame(rows, columns=[
        "product_name", "kana", "dept_id", "category", "std_price", "std_cost",
        "product_cd", "shelf_life_days", "source", "_base_qty"])
    products.insert(0, "product_id", np.arange(1, len(products) + 1))
    products["jan_code"] = [f"49{pid:011d}" for pid in products["product_id"]]
    products["_supplier_id"] = products["dept_id"].map(sd.DEPT_SUPPLIER)

    sid_of = dict(zip(stores["store_cd"], stores["store_id"]))
    competitors = pd.DataFrame(
        [(c[0], c[1], c[2], sid_of.get(c[3])) for c in COMPETITORS],
        columns=["competitor_id", "competitor_name", "business_type", "near_store_id"])
    return {
        "stores": stores,
        "departments": pd.DataFrame(sd.DEPARTMENTS, columns=["dept_id", "dept_name"]),
        "suppliers": pd.DataFrame(sd.SUPPLIERS, columns=["supplier_id", "supplier_name"]),
        "products": products,
        "competitors": competitors,
    }


# --------------------------------------------------------------------------
# 天気（データ班の期間はそのまま、それ以前は補完）
# --------------------------------------------------------------------------
def _build_weather(src: dict[str, pd.DataFrame], dates: pd.DatetimeIndex,
                   rng: np.random.Generator) -> pd.DataFrame:
    n_d = len(dates)
    doy = dates.dayofyear.to_numpy()
    tmean = 14 + 11 * np.sin((doy - 110) / 365 * 2 * np.pi)
    # 日々のずれは前日を引きずらせる（独立に振ると、気温の急変が実際より多くなりすぎる）
    eps = rng.normal(0, 3.0 * np.sqrt(1 - 0.7 ** 2), n_d)
    drift = np.empty(n_d)
    drift[0] = eps[0]
    for i in range(1, n_d):
        drift[i] = 0.7 * drift[i - 1] + eps[i]
    max_t = np.round(tmean + 4.5 + drift, 1)
    min_t = np.round(max_t - rng.uniform(7, 10, n_d), 1)
    weather = rng.choice(["晴れ", "曇り", "雨"], n_d, p=[0.45, 0.30, 0.25]).astype(object)
    weather[(weather == "雨") & (min_t < 0.5)] = "雪"

    mw = src["M_WEATHER"].drop_duplicates("date").set_index("date")
    key = dates.strftime("%Y-%m-%d")
    real = np.isin(key, mw.index)
    for i in np.where(real)[0]:
        row = mw.loc[key[i]]
        weather[i], max_t[i], min_t[i] = row["weather"], row["max_temp"], row["min_temp"]
    # 実データの前日は、実データの「前日との気温差」とつじつまが合う気温にしておく
    first = int(np.argmax(real)) if real.any() else 0
    if real.any() and first > 0:
        max_t[first - 1] = round(max_t[first] - float(mw.loc[key[first], "temp_diff_prev_day"]), 1)
        min_t[first - 1] = min(min_t[first - 1], max_t[first - 1] - 7)

    diff = np.round(np.diff(max_t, prepend=max_t[0]), 1)
    for i in np.where(real)[0]:
        diff[i] = float(mw.loc[key[i], "temp_diff_prev_day"])
    region = str(src["M_WEATHER"]["region_cd"].iloc[0]) if len(src["M_WEATHER"]) else ""
    return pd.DataFrame({
        "weather_date": dates, "area": region, "weather": weather,
        "temp_avg": np.round((max_t + min_t) / 2, 1),
        "max_temp": max_t, "min_temp": min_t, "temp_diff_prev_day": diff,
        "source": np.where(real, "データ班", "補完"),
    })


# --------------------------------------------------------------------------
# トランザクション
# --------------------------------------------------------------------------
def _cube(df: pd.DataFrame, date_col: str, value_col: str, shape: tuple,
          d_of: dict, s_of: dict, p_of: dict) -> np.ndarray:
    """(日付, 店舗, 商品) の表を、3次元の配列に置く。データが無いところは NaN。"""
    out = np.full(shape, np.nan)
    d = df[date_col].map(d_of)
    s = df["store_cd"].map(s_of)
    p = df["product_cd"].map(p_of)
    ok = d.notna() & s.notna() & p.notna()
    out[d[ok].astype(int), s[ok].astype(int), p[ok].astype(int)] = df.loc[ok, value_col].to_numpy()
    return out


def _build_transactions(src: dict[str, pd.DataFrame], masters: dict[str, pd.DataFrame],
                        dates: pd.DatetimeIndex, weather: pd.DataFrame,
                        rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    stores, products = masters["stores"], masters["products"]
    n_d, n_s, n_p = len(dates), len(stores), len(products)
    shape = (n_d, n_s, n_p)
    d_of = {d: i for i, d in enumerate(dates.strftime("%Y-%m-%d"))}
    s_of = {cd: i for i, cd in enumerate(stores["store_cd"])}
    p_of = {cd: i for i, cd in enumerate(products["product_cd"]) if isinstance(cd, str)}

    # --- データ班の実績を3次元に置く ------------------------------------
    inv, wd = src["T_INVENTORY"], src["T_WASTE_DISCOUNT"]
    r_qty = _cube(src["T_SALES"], "sales_date", "qty", shape, d_of, s_of, p_of)
    r_amount = _cube(src["T_SALES"], "sales_date", "amount", shape, d_of, s_of, p_of)
    r_stock = _cube(inv, "date", "closing_stock_qty", shape, d_of, s_of, p_of)
    r_hours = _cube(inv, "date", "out_of_stock_hours", shape, d_of, s_of, p_of)
    r_waste = _cube(wd, "date", "waste_qty", shape, d_of, s_of, p_of)
    is_real = ~np.isnan(r_qty)

    # --- 係数の準備 ------------------------------------------------------
    month_idx = dates.month.to_numpy() - 1
    # 曜日の係数は平均1にそろえる（データ班の基準販売数と水準が合うように）
    day_f = (sd.WEEKDAY / sd.WEEKDAY.mean())[dates.dayofweek.to_numpy()]   # (n_d,)
    store_f = stores["_factor"].to_numpy()                         # (n_s,)
    base_q = products["_base_qty"].to_numpy()                      # (n_p,)
    dept_of_p = products["dept_id"].to_numpy()
    season_f = np.array([[sd.SEASON[d][m] for d in dept_of_p] for m in month_idx])  # (n_d, n_p)
    trend = np.linspace(1.02, 0.96, n_d)[:, None, None]

    dept_growth = {1: 0.00, 2: 0.03, 3: -0.10, 4: 0.12, 5: -0.05, 6: -0.06}
    growth = np.array([dept_growth[d] for d in dept_of_p]) + rng.normal(0, 0.09, n_p)
    prod_trend = np.linspace(np.ones(n_p), 1 + growth, n_d)        # (n_d, n_p)

    # 気温が急に下がった日の鍋関連：AIの提案を採用する店だけ特需を取り切る
    cold = ((weather["temp_diff_prev_day"].to_numpy() <= COLD_SNAP)
            & (weather["max_temp"].to_numpy() < NABE_MAX_TEMP))    # (n_d,)
    nabe = products["product_cd"].isin(NABE_ITEMS).to_numpy()      # (n_p,)
    adopter = (stores["store_cd"] == AI_ADOPTER).to_numpy()        # (n_s,)
    cold_nabe = cold[:, None, None] & nabe[None, None, :]
    missed = cold_nabe & ~adopter[None, :, None]                   # 特需を取りこぼす店
    weather_f = np.where(cold_nabe, NABE_FACTOR, 1.0) * np.ones(shape)
    weather_f[np.broadcast_to(missed, shape)] = NABE_FACTOR_MISSED

    factor = (base_q[None, None, :] * store_f[None, :, None] * day_f[:, None, None]
              * season_f[:, None, :] * prod_trend[:, None, :] * trend * weather_f)

    if DRUGSTORE_STORE in s_of:
        after = np.asarray(dates >= DRUGSTORE_FROM)
        hit = np.where(np.isin(dept_of_p, [5, 6]))[0]
        factor[np.ix_(after, [s_of[DRUGSTORE_STORE]], hit)] *= 0.78

    promo = np.broadcast_to(rng.random((n_d, 1, n_p)) < 0.06, shape).copy()
    promo[is_real] = False
    lam = factor * np.where(promo, 1.9, 1.0) * rng.normal(1.0, 0.12, shape).clip(0.4, 1.8)
    qty = rng.poisson(lam.clip(0.05, None)).astype(np.int64)
    qty[is_real] = r_qty[is_real].astype(np.int64)                 # 実績はそのまま

    d_idx, s_idx, p_idx = np.nonzero(qty)
    cell = (d_idx, s_idx, p_idx)
    q = qty[cell]
    pr = promo[cell]
    real_row = is_real[cell]
    price = products["std_price"].to_numpy()[p_idx] * np.where(pr, 0.82, 1.0)
    cost = products["std_cost"].to_numpy()[p_idx]
    amount = np.where(real_row, r_amount[cell], np.round(price * q, 0))

    sales = pd.DataFrame({
        "sales_date": dates.to_numpy()[d_idx],
        "store_id": stores["store_id"].to_numpy()[s_idx],
        "product_id": products["product_id"].to_numpy()[p_idx],
        "qty": q,
        "amount": amount,
        "gross_profit": np.round(amount - cost * q, 0),
        "is_promo": pr.astype(np.int8),
    })
    dept_row = dept_of_p[p_idx]

    # --- 在庫 -------------------------------------------------------------
    recent_from = dates[-1] - pd.Timedelta(days=90)
    keep = (sales["sales_date"] >= recent_from).to_numpy() | (rng.random(len(sales)) < 0.25) | real_row
    days_arr = pd.Series(dept_row).map(STOCK_DAYS).to_numpy()
    stock = rng.poisson(q * days_arr + 1)
    stock = np.where(real_row & ~np.isnan(r_stock[cell]), np.nan_to_num(r_stock[cell]), stock)
    inventory = pd.DataFrame({
        "inv_date": sales["sales_date"].to_numpy()[keep],
        "store_id": sales["store_id"].to_numpy()[keep],
        "product_id": sales["product_id"].to_numpy()[keep],
        "stock_qty": stock[keep].astype(np.int64),
    })

    # --- 廃棄：データ班の商品は実績の廃棄率、補完の商品は部門ごとの率 ------
    sum_q = np.nansum(r_qty, axis=(0, 1))
    real_rate = np.divide(np.nansum(r_waste, axis=(0, 1)), sum_q,
                          out=np.zeros(n_p), where=sum_q > 0)
    is_real_p = products["source"].eq("データ班").to_numpy()
    rate_p = np.where(is_real_p, real_rate, np.where(np.isin(dept_of_p, list(sd.WASTE_DEPTS)), 0.035, 0.0))
    rate = rate_p[p_idx]
    if OVERPRODUCTION_STORE in s_of:
        rate = np.where((s_idx == s_of[OVERPRODUCTION_STORE]) & (dept_row == 4), 0.11, rate)
    w_qty = rng.poisson(q * rate)
    w_qty = np.where(real_row, np.nan_to_num(r_waste[cell]), w_qty).astype(np.int64)
    w_mask = w_qty > 0
    waste = pd.DataFrame({
        "waste_date": sales["sales_date"].to_numpy()[w_mask],
        "store_id": sales["store_id"].to_numpy()[w_mask],
        "product_id": sales["product_id"].to_numpy()[w_mask],
        "waste_qty": w_qty[w_mask],
        "waste_amount": np.round(w_qty[w_mask] * cost[w_mask], 0),
    })

    # --- 欠品：データ班の商品は実績の欠品率。特需を取りこぼした日は多くなる ----
    out = r_hours > 0
    missed_real = is_real & np.broadcast_to(missed, shape)
    normal_real = is_real & ~np.broadcast_to(missed, shape)
    n_normal = normal_real.sum(axis=(0, 1))
    so_real = np.divide((out & normal_real).sum(axis=(0, 1)), n_normal,
                        out=np.zeros(n_p), where=n_normal > 0)
    p_missed = float((out & missed_real).sum() / missed_real.sum()) if missed_real.any() else 0.6
    so_p = np.where(is_real_p[p_idx], so_real[p_idx], 0.012 + 0.03 * pr)
    so_p = np.where(missed[cell], max(p_missed, 0.0), so_p)
    so_mask = np.where(real_row, out[cell], rng.random(len(sales)) < so_p)
    slots = rng.choice(np.array(["午前", "午後", "夕方", "夜"]), len(sales), p=[0.15, 0.25, 0.40, 0.20])
    # 実績は「欠品していた時間」から時間帯を決める（長いほど早い時間から欠品している）
    hours = np.nan_to_num(r_hours[cell])
    slots = np.where(real_row, np.where(hours >= 5, "午後", np.where(hours >= 2, "夕方", "夜")), slots)
    stockouts = pd.DataFrame({
        "stockout_date": sales["sales_date"].to_numpy()[so_mask],
        "time_slot": slots[so_mask],
        "store_id": sales["store_id"].to_numpy()[so_mask],
        "product_id": sales["product_id"].to_numpy()[so_mask],
    })

    # --- 仕入：補完ぶんは週1回（月曜）にまとめて発注、実績は T_ORDER のとおり ----
    cost_by_pid = dict(zip(products["product_id"], products["std_cost"]))
    sup_by_pid = dict(zip(products["product_id"], products["_supplier_id"]))
    wk = sales.loc[~real_row, ["sales_date", "store_id", "product_id", "qty"]].copy()
    wk["purchase_date"] = wk["sales_date"] - pd.to_timedelta(wk["sales_date"].dt.dayofweek, unit="D")
    purchases = (wk.groupby(["purchase_date", "store_id", "product_id"], as_index=False)["qty"].sum()
                   .rename(columns={"qty": "purchase_qty"}))
    purchases["purchase_qty"] = (
        purchases["purchase_qty"] * rng.normal(1.05, 0.08, len(purchases))).round().astype(int).clip(1, None)
    months = ((purchases["purchase_date"].dt.year - dates[0].year) * 12
              + purchases["purchase_date"].dt.month - dates[0].month)
    purchases["unit_cost"] = np.round(
        purchases["product_id"].map(cost_by_pid).to_numpy()
        * (1.0 + months.to_numpy() * 0.0025) * rng.normal(1.0, 0.03, len(purchases)), 1)
    od = src["T_ORDER"]
    orders = pd.DataFrame({
        "purchase_date": pd.to_datetime(od["delivery_date"]),
        "store_id": od["store_cd"].map(dict(zip(stores["store_cd"], stores["store_id"]))),
        "product_id": od["product_cd"].map(dict(zip(products["product_cd"], products["product_id"]))),
        "purchase_qty": od["final_order_qty"],
    }).dropna()
    orders["unit_cost"] = orders["product_id"].map(cost_by_pid)
    purchases = pd.concat([purchases, orders], ignore_index=True)
    purchases["store_id"] = purchases["store_id"].astype(int)
    purchases["product_id"] = purchases["product_id"].astype(int)
    purchases["supplier_id"] = purchases["product_id"].map(sup_by_pid)

    # --- 客数：売上規模に合うように逆算する（データ班のデータには無い）------
    base = float(sales["amount"].sum()) / (n_d * store_f.sum()) / TARGET_SPEND
    cust = []
    for sid, f in zip(stores["store_id"], store_f):
        n = base * f * day_f * np.linspace(1.02, 0.95, n_d) * rng.normal(1.0, 0.07, n_d)
        cust.append(pd.DataFrame({
            "visit_date": dates, "store_id": sid,
            "customer_count": n.round().astype(int),
            "member_count": (n * rng.uniform(0.55, 0.68, n_d)).round().astype(int),
        }))
    customers_daily = pd.concat(cust, ignore_index=True)

    # --- 予算：実績をもとに「ほぼ達成する前提の計画値」を置く --------------
    s2 = sales[["sales_date", "store_id", "amount", "gross_profit"]].copy()
    s2["dept_id"] = dept_row
    s2["year_month"] = s2["sales_date"].dt.strftime("%Y-%m")
    budgets = (s2.groupby(["year_month", "store_id", "dept_id"], as_index=False)
                 .agg(sales_budget=("amount", "sum"), gp_budget=("gross_profit", "sum")))
    noise = rng.normal(1.01, 0.05, len(budgets)).clip(0.9, 1.15)
    budgets["sales_budget"] = (budgets["sales_budget"] * noise).round()
    budgets["gp_budget"] = (budgets["gp_budget"] * noise).round()

    # --- 競合価格（クローラーで取る想定のテーブル。ここでは生成データ）------
    weeks = pd.date_range(dates[0], dates[-1], freq="W-MON")
    rec, seq = [], 1
    for _, p in products.sample(min(24, n_p), random_state=7).iterrows():
        for c in COMPETITORS:
            if c[2] == "ドラッグストア" and p["dept_id"] in (5, 6):
                ratio = rng.uniform(0.82, 0.90)
            else:
                ratio = rng.uniform(0.94, 1.08)
            for w in weeks:
                rec.append((seq, w.strftime("%Y-%m-%d 06:00:00"), c[0], p["product_name"],
                            int(p["product_id"]),
                            float(np.round(p["std_price"] * ratio * rng.normal(1.0, 0.05), 0)),
                            f"https://example.com/chirashi/{c[0]}/{w:%Y%m%d}"))
                seq += 1
    competitor_prices = pd.DataFrame(rec, columns=[
        "price_id", "fetched_at", "competitor_id", "raw_name", "product_id", "price", "source_url"])

    return {
        "sales": sales, "inventory": inventory, "waste": waste, "stockouts": stockouts,
        "purchases": purchases, "customers_daily": customers_daily, "budgets": budgets,
        "competitor_prices": competitor_prices,
    }


def _knowledge(masters: dict[str, pd.DataFrame]) -> pd.DataFrame:
    name = dict(zip(masters["stores"]["store_cd"], masters["stores"]["store_name"]))
    at = lambda cd: name.get(cd, "本部")      # noqa: E731
    rows = [
        (1, "雨の日の惣菜は14時までに作り切る",
         "雨の日は夕方の客数が2割落ちる。揚物は14時以降の追加調理を止めて、15時からの値引きを早めると廃棄がほぼ出ない。",
         4, "惣菜,廃棄,天気", "", f"{at('ST005')} 山本", "2026-06-12", 12),
        (2, "特売バナナは入口平台で2段積み",
         "バナナの特売は入口の平台に2段で積むと、通常什器の1.6倍売れた。POPは黄色地に黒文字で価格だけ大きく。",
         1, "青果,売場,POP", "", f"{at('ST001')} 佐藤", "2026-05-28", 9),
        (3, "夕方の刺身は3点盛りに組み替える",
         "16時時点で単品の刺身が残ったら、値引きより先に3点盛りへ組み替える方が粗利が残る。",
         3, "鮮魚,値引き,粗利", "", f"{at('ST004')} 鈴木", "2026-07-03", 15),
        (4, "牛乳の欠品は金曜夕方に集中",
         "金曜の夕方に牛乳1Lが欠品しやすい。木曜の発注を1.3倍にしたら欠品がゼロになった。",
         5, "日配,欠品,発注", "", f"{at('ST003')} 田中", "2026-08-19", 7),
        (5, "ドラッグストア対抗は日配の底値追随をやめる",
         "日配は価格ではドラッグストアに勝てない。地場の豆腐・納豆を前面に出したら粗利率が1.2pt改善した。",
         5, "日配,競合,価格", "", f"{at('ST002')} 高橋", "2026-09-02", 18),
        (6, "新人向け：発注端末の入力手順",
         "発注端末は前年同週の実績を見てから入力する。特売週は前年の特売週と比べること。",
         6, "新人教育,発注", "", f"{at('ST001')} 伊藤", "2026-04-10", 5),
        (7, "気温が急に下がる日は鍋つゆ・豆腐を多めに発注",
         "前日より3℃以上下がる予報の日は、鍋つゆ・木綿豆腐・鍋用の肉と野菜が1.4倍ほど売れる。"
         "前週並みの発注だと夕方には欠品するので、AIの発注提案どおりに増やす。",
         5, "日配,欠品,発注,天気", "", f"{at('ST001')} 伊藤", "2026-09-24", 10),
    ]
    return pd.DataFrame(rows, columns=["knowledge_id", "title", "body", "dept_id", "tags",
                                       "photo_path", "author", "created_at", "helpful_count"])


# --------------------------------------------------------------------------
# 公開API
# --------------------------------------------------------------------------
def generate(db_path: str | Path, progress=None) -> Path:
    """データ班のCSVを正として、画面用のテーブル一式を SQLite ファイルに書き出す。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    rng = np.random.default_rng(SEED)
    if progress:
        progress("データ班のCSVを読み込み中…", 0.1)
    src = {t: _csv(t) for t in REQUIRED}
    real_dates = sorted(src["T_SALES"]["sales_date"].unique())
    end = pd.Timestamp(real_dates[-1]) + pd.offsets.MonthEnd(0)       # その月の月末まで
    start = end + pd.Timedelta(days=1) - pd.DateOffset(years=YEARS)   # 2年前の月初から
    dates = pd.date_range(start, end, freq="D")

    masters = _build_masters(src)
    weather = _build_weather(src, dates, rng)
    if progress:
        progress("足りない期間・商品のデータを補完中…", 0.3)
    trans = _build_transactions(src, masters, dates, weather, rng)
    if progress:
        progress("データベースに書き込み中…", 0.75)

    tables: dict[str, pd.DataFrame] = dict(masters)
    tables["stores"] = tables["stores"].drop(columns=["_factor"])
    tables["products"] = tables["products"].drop(columns=["_base_qty", "_supplier_id"])
    tables.update(trans)
    tables["weather_daily"] = weather
    tables["knowledge"] = _knowledge(masters)
    n_real = int(masters["products"]["source"].eq("データ班").sum())
    tables["db_meta"] = pd.DataFrame([
        ("source", "データ班のCSV＋補完"),
        ("real_from", real_dates[0]),
        ("real_to", real_dates[-1]),
        ("stores", str(len(masters["stores"]))),
        ("real_products", str(n_real)),
        ("added_products", str(len(masters["products"]) - n_real)),
    ], columns=["key", "value"])

    con = sqlite3.connect(db_path)
    try:
        for name, df in tables.items():
            out = df.copy()
            for col in out.columns:
                if pd.api.types.is_datetime64_any_dtype(out[col]):
                    out[col] = out[col].dt.strftime("%Y-%m-%d")
            out.to_sql(name, con, index=False, if_exists="replace", chunksize=20_000)
        for sql in sd.INDEXES:
            con.execute(sql)
        con.commit()
    finally:
        con.close()
    if progress:
        progress("完了", 1.0)
    return db_path
