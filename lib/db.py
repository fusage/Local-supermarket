# -*- coding: utf-8 -*-
"""データアクセス層（SQLiteへの接続と集計クエリ）

【データの置き場所】
1. 環境変数 SUPERMARKET_DB が指すファイル
2. data/supermarket.db        ← 藤井さん担当の本番ダミーデータを置く場所
3. data/supermarket_dummy.db  ← 上が無いとき、lib/sample_data.py が自動生成する暫定データ

1→3の順に探すので、2に本番データを置けばコードを触らずに切り替わります（lib/paths.py）。
テーブル名・列名は要件定義書 5.1.1 のER図に合わせてあります。

同じDBファイルに、データ班のテーブル（M_* / T_* と分析ビュー V_*）と
クローラーの保存先（crawl_*）も入っています（lib/integrate.py が追加します）。
"""
from __future__ import annotations

import logging
import sqlite3

import pandas as pd
import streamlit as st

from lib import gcp
from lib.paths import APP_DIR, DATA_DIR, DUMMY_DB, REAL_DB, resolve_db_path  # noqa: F401


@st.cache_resource(show_spinner=False)
def get_conn(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path, check_same_thread=False)


def _conn() -> sqlite3.Connection:
    path, _ = resolve_db_path()
    return get_conn(str(path))


@st.cache_data(show_spinner=False)
def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    """SQLを実行してDataFrameで返す（結果はキャッシュされる）。"""
    return pd.read_sql_query(sql, _conn(), params=params)


# --------------------------------------------------------------------------
# 絞り込み条件の組み立て
# --------------------------------------------------------------------------
def _ph(n: int) -> str:
    return ",".join("?" * n)


def scope(date_col: str, start: str, end: str,
          stores: tuple[int, ...] = (), depts: tuple[int, ...] = (),
          product_table: str = "p") -> tuple[str, list]:
    """WHERE句とパラメータを組み立てる。部門で絞るときは products との JOIN が必要。"""
    where = [f"{date_col} BETWEEN ? AND ?"]
    params: list = [start, end]
    if stores:
        where.append(f"store_id IN ({_ph(len(stores))})")
        params += list(stores)
    if depts:
        where.append(f"{product_table}.dept_id IN ({_ph(len(depts))})")
        params += list(depts)
    return " AND ".join(where), params


def shift_year(d: str, years: int = 1) -> str:
    """'2026-09-30' → '2025-09-30'（前年同期比の計算用）。"""
    ts = pd.Timestamp(d) - pd.DateOffset(years=years)
    return ts.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# マスタ
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def masters() -> dict[str, pd.DataFrame]:
    return {
        "stores": q("SELECT * FROM stores ORDER BY store_id"),
        "departments": q("SELECT * FROM departments ORDER BY dept_id"),
        "products": q(
            "SELECT p.*, d.dept_name FROM products p "
            "JOIN departments d USING(dept_id) ORDER BY p.product_id"
        ),
    }


@st.cache_data(show_spinner=False)
def date_bounds() -> tuple[str, str]:
    df = q("SELECT MIN(sales_date) a, MAX(sales_date) b FROM sales")
    return df.a[0], df.b[0]


# --------------------------------------------------------------------------
# 経営ダッシュボード
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def kpi(start: str, end: str, stores: tuple = (), depts: tuple = ()) -> dict:
    """売上・粗利・客数などの主要指標を、前年同期・予算と合わせて返す。"""
    w, p = scope("s.sales_date", start, end, stores, depts)
    sql = (f"SELECT SUM(s.amount) amount, SUM(s.gross_profit) gp, SUM(s.qty) qty "
           f"FROM sales s JOIN products p USING(product_id) WHERE {w}")
    cur = q(sql, tuple(p)).iloc[0]

    w2, p2 = scope("s.sales_date", shift_year(start), shift_year(end), stores, depts)
    prev = q(sql.replace(w, w2), tuple(p2)).iloc[0]

    # 客数（部門での絞り込みは客数には効かない＝店舗単位の指標のため）
    wc = ["visit_date BETWEEN ? AND ?"]
    pc: list = [start, end]
    if stores:
        wc.append(f"store_id IN ({_ph(len(stores))})")
        pc += list(stores)
    cust = q("SELECT SUM(customer_count) c FROM customers_daily WHERE " + " AND ".join(wc),
             tuple(pc)).c[0]

    # 予算（期間に含まれる年月ぶん）
    wb = ["year_month BETWEEN ? AND ?"]
    pb: list = [start[:7], end[:7]]
    if stores:
        wb.append(f"store_id IN ({_ph(len(stores))})")
        pb += list(stores)
    if depts:
        wb.append(f"dept_id IN ({_ph(len(depts))})")
        pb += list(depts)
    bud = q("SELECT SUM(sales_budget) sb, SUM(gp_budget) gb FROM budgets WHERE "
            + " AND ".join(wb), tuple(pb)).iloc[0]

    amount = float(cur.amount or 0)
    gp = float(cur.gp or 0)
    cust = float(cust or 0)
    return {
        "amount": amount,
        "gross_profit": gp,
        "gp_rate": gp / amount * 100 if amount else 0.0,
        "customers": cust,
        "spend": amount / cust if cust else 0.0,
        "prev_amount": float(prev.amount or 0),
        "prev_gp": float(prev.gp or 0),
        "budget_amount": float(bud.sb or 0),
        "budget_gp": float(bud.gb or 0),
    }


@st.cache_data(show_spinner=False)
def monthly_trend(start: str, end: str, stores: tuple = (), depts: tuple = ()) -> pd.DataFrame:
    """月次の売上・粗利と、前年同月・予算を1つの表にまとめる。"""
    w, p = scope("s.sales_date", shift_year(start), end, stores, depts)
    cur = q(
        f"SELECT substr(s.sales_date,1,7) ym, SUM(s.amount) amount, SUM(s.gross_profit) gp "
        f"FROM sales s JOIN products p USING(product_id) WHERE {w} GROUP BY 1 ORDER BY 1",
        tuple(p),
    )
    wb = ["year_month BETWEEN ? AND ?"]
    pb: list = [start[:7], end[:7]]
    if stores:
        wb.append(f"store_id IN ({_ph(len(stores))})")
        pb += list(stores)
    if depts:
        wb.append(f"dept_id IN ({_ph(len(depts))})")
        pb += list(depts)
    bud = q("SELECT year_month ym, SUM(sales_budget) budget FROM budgets WHERE "
            + " AND ".join(wb) + " GROUP BY 1", tuple(pb))

    cur = cur.copy()
    cur["prev_ym"] = cur["ym"].map(lambda x: shift_year(x + "-01")[:7])
    prev_map = dict(zip(cur["ym"], cur["amount"]))
    cur["prev_amount"] = cur["prev_ym"].map(prev_map)
    out = cur.merge(bud, on="ym", how="left")
    return out[out["ym"] >= start[:7]].reset_index(drop=True)


@st.cache_data(show_spinner=False)
#def by_store(start: str, end: str, depts: tuple = ()) -> pd.DataFrame:
#    """店舗別の売上・粗利率・前年比。"""
#    w, p = scope("s.sales_date", start, end, (), depts)
#    cur = q(
#        f"SELECT st.store_id, st.store_name, SUM(s.amount) amount, SUM(s.gross_profit) gp "
#        f"FROM sales s JOIN products p USING(product_id) JOIN stores st USING(store_id) "
#        f"WHERE {w} GROUP BY 1,2", tuple(p))
#    w2, p2 = scope("s.sales_date", shift_year(start), shift_year(end), (), depts)
#    prev = q(
#        f"SELECT st.store_id, SUM(s.amount) prev_amount "
#        f"FROM sales s JOIN products p USING(product_id) JOIN stores st USING(store_id) "
#        f"WHERE {w2} GROUP BY 1", tuple(p2))
#    out = cur.merge(prev, on="store_id", how="left")
#    out["gp_rate"] = out["gp"] / out["amount"] * 100
#    out["yoy"] = (out["amount"] / out["prev_amount"] - 1) * 100
#    return out.sort_values("amount", ascending=False).reset_index(drop=True)


@st.cache_data(show_spinner=False)
#def by_dept(start: str, end: str, stores: tuple = ()) -> pd.DataFrame:
#    """部門別の売上構成と粗利率・前年比。"""
#    w, p = scope("s.sales_date", start, end, stores)
#    cur = q(
#        f"SELECT d.dept_id, d.dept_name, SUM(s.amount) amount, SUM(s.gross_profit) gp "
#        f"FROM sales s JOIN products p USING(product_id) JOIN departments d USING(dept_id) "
#        f"WHERE {w} GROUP BY 1,2", tuple(p))
#    w2, p2 = scope("s.sales_date", shift_year(start), shift_year(end), stores)
#    prev = q(
#        f"SELECT p.dept_id, SUM(s.amount) prev_amount "
#        f"FROM sales s JOIN products p USING(product_id) WHERE {w2} GROUP BY 1", tuple(p2))
#    out = cur.merge(prev, on="dept_id", how="left")
#    out["share"] = out["amount"] / out["amount"].sum() * 100
#    out["gp_rate"] = out["gp"] / out["amount"] * 100
#    out["yoy"] = (out["amount"] / out["prev_amount"] - 1) * 100
#    return out.sort_values("amount", ascending=False).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def loss_summary(start: str, end: str, stores: tuple = (), depts: tuple = ()) -> pd.DataFrame:
    """店舗×部門のロス率・欠品件数（悪い順に並べる）。"""
    ws, ps = scope("s.sales_date", start, end, stores, depts)
    sales = q(
        f"SELECT st.store_name, d.dept_name, SUM(s.amount) amount "
        f"FROM sales s JOIN products p USING(product_id) JOIN departments d USING(dept_id) "
        f"JOIN stores st USING(store_id) WHERE {ws} GROUP BY 1,2", tuple(ps))
    ww, pw = scope("w.waste_date", start, end, stores, depts)
    waste = q(
        f"SELECT st.store_name, d.dept_name, SUM(w.waste_amount) waste "
        f"FROM waste w JOIN products p USING(product_id) JOIN departments d USING(dept_id) "
        f"JOIN stores st USING(store_id) WHERE {ww} GROUP BY 1,2", tuple(pw))
    wo, po = scope("o.stockout_date", start, end, stores, depts)
    so = q(
        f"SELECT st.store_name, d.dept_name, COUNT(*) stockouts "
        f"FROM stockouts o JOIN products p USING(product_id) JOIN departments d USING(dept_id) "
        f"JOIN stores st USING(store_id) WHERE {wo} GROUP BY 1,2", tuple(po))
    out = sales.merge(waste, on=["store_name", "dept_name"], how="left") \
               .merge(so, on=["store_name", "dept_name"], how="left")
    out[["waste", "stockouts"]] = out[["waste", "stockouts"]].fillna(0)
    out["loss_rate"] = out["waste"] / out["amount"] * 100
    return out.sort_values("loss_rate", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------
# バイヤーダッシュボード
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def product_perf(start: str, end: str, stores: tuple = (), depts: tuple = ()) -> pd.DataFrame:
    """商品別の売上・数量・粗利率・前年比。"""
    w, p = scope("s.sales_date", start, end, stores, depts)
    cur = q(
        f"SELECT p.product_id, p.product_name, d.dept_name, p.category, p.std_price, "
        f"SUM(s.qty) qty, SUM(s.amount) amount, SUM(s.gross_profit) gp "
        f"FROM sales s JOIN products p USING(product_id) JOIN departments d USING(dept_id) "
        f"WHERE {w} GROUP BY 1,2,3,4,5", tuple(p))
    w2, p2 = scope("s.sales_date", shift_year(start), shift_year(end), stores, depts)
    prev = q(
        f"SELECT p.product_id, SUM(s.amount) prev_amount "
        f"FROM sales s JOIN products p USING(product_id) WHERE {w2} GROUP BY 1", tuple(p2))
    out = cur.merge(prev, on="product_id", how="left")
    out["gp_rate"] = out["gp"] / out["amount"] * 100
    out["yoy"] = (out["amount"] / out["prev_amount"] - 1) * 100
    return out.sort_values("amount", ascending=False).reset_index(drop=True)


#def abc_analysis(perf: pd.DataFrame, key: str = "amount") -> pd.DataFrame:
#    """売上（または粗利）の多い順にA/B/Cへ分類する。A=上位70%、B=〜90%、C=残り。"""
#    df = perf.sort_values(key, ascending=False).reset_index(drop=True).copy()
#    total = df[key].sum()
#    df["cum_share"] = df[key].cumsum() / total * 100 if total else 0
#    df["rank_class"] = pd.cut(df["cum_share"], [-1, 70, 90, 1000], labels=["A", "B", "C"])
#    return df


@st.cache_data(show_spinner=False)
#def cost_trend(start: str, end: str, stores: tuple = (), depts: tuple = ()) -> pd.DataFrame:
#    """月次の平均仕入単価（標準原価を1.00とした指数）。"""
#    w, p = scope("pu.purchase_date", start, end, stores, depts)
#    return q(
#        f"SELECT substr(pu.purchase_date,1,7) ym, d.dept_name, "
#        f"SUM(pu.unit_cost*pu.purchase_qty)/SUM(pu.purchase_qty) avg_cost, "
#        f"SUM(p.std_cost*pu.purchase_qty)/SUM(pu.purchase_qty) std_cost "
#        f"FROM purchases pu JOIN products p USING(product_id) "
#        f"JOIN departments d USING(dept_id) WHERE {w} GROUP BY 1,2 ORDER BY 1", tuple(p))


@st.cache_data(show_spinner=False)
def competitor_gap(depts: tuple = ()) -> pd.DataFrame:
    """商品ごとの自社売価と競合の最新価格を比べる（クローラーで集めたデータを使用）。"""
    where = ""
    params: list = []
    if depts:
        where = f"WHERE p.dept_id IN ({_ph(len(depts))})"
        params = list(depts)
    return q(
        f"""
        WITH latest AS (
            SELECT product_id, competitor_id, MAX(fetched_at) mx FROM competitor_prices
            GROUP BY product_id, competitor_id
        )
        SELECT p.product_name, d.dept_name, p.std_price,
               c.competitor_name, c.business_type, cp.price, cp.fetched_at
        FROM competitor_prices cp
        JOIN latest l ON l.product_id=cp.product_id AND l.competitor_id=cp.competitor_id
                      AND l.mx=cp.fetched_at
        JOIN products p ON p.product_id=cp.product_id
        JOIN departments d ON d.dept_id=p.dept_id
        JOIN competitors c ON c.competitor_id=cp.competitor_id
        {where}
        """, tuple(params))


# --------------------------------------------------------------------------
# 部門担当者ダッシュボード
# --------------------------------------------------------------------------
# 商品ごとに「基準日以前でいちばん新しい在庫レコード」を取る。
# 全商品の在庫が毎日そろっているとは限らないため、日付を決め打ちにしない。
LATEST_STOCK_SQL = """
    SELECT i.product_id, i.stock_qty, i.inv_date
    FROM inventory i
    JOIN (SELECT product_id, MAX(inv_date) mx FROM inventory
          WHERE store_id=? AND inv_date<=? GROUP BY product_id) t
      ON t.product_id=i.product_id AND t.mx=i.inv_date
    WHERE i.store_id=?
"""
@st.cache_data(show_spinner=False)
def daily_progress(start: str, end: str, store_id: int, dept_id: int) -> pd.DataFrame:
    """日次の売上推移（自店舗・自部門）。"""
    return q(
        "SELECT s.sales_date, SUM(s.amount) amount, SUM(s.gross_profit) gp, SUM(s.qty) qty "
        "FROM sales s JOIN products p USING(product_id) "
        "WHERE s.sales_date BETWEEN ? AND ? AND s.store_id=? AND p.dept_id=? "
        "GROUP BY 1 ORDER BY 1", (start, end, store_id, dept_id))


@st.cache_data(show_spinner=False)
def waste_ranking(start: str, end: str, store_id: int, dept_id: int) -> pd.DataFrame:
    return q(
        "SELECT p.product_name, SUM(w.waste_qty) qty, SUM(w.waste_amount) amount "
        "FROM waste w JOIN products p USING(product_id) "
        "WHERE w.waste_date BETWEEN ? AND ? AND w.store_id=? AND p.dept_id=? "
        "GROUP BY 1 ORDER BY 3 DESC LIMIT 15", (start, end, store_id, dept_id))


@st.cache_data(show_spinner=False)
def stockout_detail(start: str, end: str, store_id: int, dept_id: int) -> pd.DataFrame:
    return q(
        "SELECT p.product_name, o.time_slot, COUNT(*) cnt "
        "FROM stockouts o JOIN products p USING(product_id) "
        "WHERE o.stockout_date BETWEEN ? AND ? AND o.store_id=? AND p.dept_id=? "
        "GROUP BY 1,2 ORDER BY 3 DESC", (start, end, store_id, dept_id))


@st.cache_data(show_spinner=False)
#def reorder_suggestion(as_of: str, store_id: int, dept_id: int) -> pd.DataFrame:
#    """発注数の目安＝過去4週の同じ曜日の平均販売数 − 現在庫。
#
#    ※ ごく単純な計算です。需要予測モデルは今回のスコープ外（要件定義 D-05）。
#    """
#    dow = pd.Timestamp(as_of).dayofweek
#    since = (pd.Timestamp(as_of) - pd.Timedelta(days=28)).strftime("%Y-%m-%d")
#    hist = q(
#        "SELECT p.product_id, p.product_name, s.sales_date, s.qty "
#        "FROM sales s JOIN products p USING(product_id) "
#        "WHERE s.sales_date BETWEEN ? AND ? AND s.store_id=? AND p.dept_id=?",
#        (since, as_of, store_id, dept_id))
#    if hist.empty:
#        return hist
#    hist = hist.copy()
#    hist["dow"] = pd.to_datetime(hist["sales_date"]).dt.dayofweek
#    same = hist[hist["dow"] == dow]
#    base = (same.groupby(["product_id", "product_name"], as_index=False)["qty"].mean()
#                .rename(columns={"qty": "avg_qty"}))
#    stock = q(LATEST_STOCK_SQL, (store_id, as_of, store_id))
#    out = base.merge(stock, on="product_id", how="left")
#    out["stock_qty"] = out["stock_qty"].fillna(0)
#    out["suggest"] = (out["avg_qty"] * 1.1 - out["stock_qty"]).round().clip(lower=0)
#    return out.sort_values("suggest", ascending=False).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def inventory_status(as_of: str, store_id: int, dept_id: int) -> pd.DataFrame:
    """在庫数と在庫日数（直近7日の平均販売数で割った日数）。"""
    since = (pd.Timestamp(as_of) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    return q(
        f"""
        WITH last_inv AS ({LATEST_STOCK_SQL}
        ), recent AS (
            SELECT product_id, AVG(qty) avg_qty FROM sales
            WHERE store_id=? AND sales_date BETWEEN ? AND ? GROUP BY product_id
        )
        SELECT p.product_name, li.stock_qty, r.avg_qty,
               CASE WHEN r.avg_qty>0 THEN li.stock_qty/r.avg_qty END days
        FROM last_inv li JOIN products p USING(product_id)
        LEFT JOIN recent r ON r.product_id=li.product_id
        WHERE p.dept_id=? ORDER BY days DESC
        """, (store_id, as_of, store_id, store_id, since, as_of, dept_id))


# --------------------------------------------------------------------------
# 検索・ナレッジ
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def find_products(keyword: str) -> pd.DataFrame:
    """商品名・読み仮名・カテゴリの部分一致検索。"""
    like = f"%{keyword}%"
    return q(
        "SELECT p.product_id, p.product_name, p.kana, d.dept_name, p.category, p.std_price "
        "FROM products p JOIN departments d USING(dept_id) "
        "WHERE p.product_name LIKE ? OR p.kana LIKE ? OR p.category LIKE ? OR d.dept_name LIKE ? "
        "LIMIT 20", (like, like, like, like))


@st.cache_data(show_spinner=False)
def product_sales_trend(product_id: int, start: str, end: str, stores: tuple = ()) -> pd.DataFrame:
    w = ["s.sales_date BETWEEN ? AND ?", "s.product_id=?"]
    p: list = [start, end, product_id]
    if stores:
        w.append(f"s.store_id IN ({_ph(len(stores))})")
        p += list(stores)
    return q(
        "SELECT substr(s.sales_date,1,7) ym, SUM(s.qty) qty, SUM(s.amount) amount, "
        "SUM(s.gross_profit) gp FROM sales s WHERE " + " AND ".join(w) + " GROUP BY 1 ORDER BY 1",
        tuple(p))


@st.cache_data(show_spinner=False)
#def knowledge(keyword: str = "", dept_id: int | None = None) -> pd.DataFrame:
#    w, p = [], []
#    if keyword:
#        like = f"%{keyword}%"
#        w.append("(k.title LIKE ? OR k.body LIKE ? OR k.tags LIKE ?)")
#        p += [like, like, like]
#    if dept_id:
#        w.append("k.dept_id=?")
#        p.append(dept_id)
#    where = ("WHERE " + " AND ".join(w)) if w else ""
#    return q(
#        f"SELECT k.knowledge_id, k.title, k.body, d.dept_name, k.tags, k.author, "
#        f"k.created_at, k.helpful_count FROM knowledge k "
#        f"LEFT JOIN departments d USING(dept_id) {where} "
#        f"ORDER BY k.helpful_count DESC", tuple(p))


@st.cache_data(show_spinner=False)
def table_exists(name: str) -> bool:
    """テーブル（またはビュー）があるかどうか。"""
    df = q("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,))
    return not df.empty


@st.cache_data(show_spinner=False)
def db_meta() -> dict[str, str]:
    """DBの素性（lib/real_data.py が組み立てたDBなら、実データの期間や商品数が入っている）。"""
    if not table_exists("db_meta"):
        return {}
    df = q("SELECT key, value FROM db_meta")
    return dict(zip(df["key"], df["value"]))


# --------------------------------------------------------------------------
# ロス分析（データ班の分析ビュー。定義は scripts/analysis_views.sql）
#   GCPの設定があれば BigQuery（scripts/gcp_setup.py が作ったもの）から読む。
#   BigQuery に届かないときは、手元の SQLite に切り替えて画面を出し続ける。
# --------------------------------------------------------------------------
#@st.cache_data(show_spinner=False)
#def loss_period() -> tuple[str, str]:
#    """データ班のデータが入っている期間。"""
#    df = q("SELECT MIN(sales_date) a, MAX(sales_date) b FROM T_SALES")
#    return df.a[0], df.b[0]


#def get_loss_summary() -> pd.DataFrame:
#    """店舗別の3大ロス（廃棄・値引・機会ロス）集計。"""
#    return q("SELECT * FROM V_STORE_LOSS_SUMMARY")


#def get_weather_hypothesis() -> pd.DataFrame:
#    """気温急変日（前日差 −2.5℃以下）の発注と、その結果。"""
#    return q("SELECT * FROM V_WEATHER_HYPOTHESIS_CHECK")


def get_discount_candidates() -> pd.DataFrame:
    """夕方の見切り推奨候補（日持ち2日以内の商品）。"""
    return loss_q("SELECT * FROM V_EVENING_DISCOUNT_CANDIDATES")
