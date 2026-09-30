# -*- coding: utf-8 -*-
"""データの統合（3人の成果物を1つのSQLiteファイルにまとめる）

  A フロントエンドのDB   … stores / products / sales など（ER図のテーブル）
  B データ班のDB         … M_STORE / T_SALES など7テーブル ＋ 分析ビュー3本
  C クローラーの取得結果 … crawl_flyers / crawl_featured_items / crawl_events

AのDBファイルに、BのCSV（data/csv/*.csv）とCの保存用テーブルを足します。
何度呼んでも同じ結果になる（足りないものだけ作る）ので、起動のたびに呼んで構いません。
このファイルは streamlit を読み込まないので、scripts/ からも使えます。
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from lib.paths import CSV_DIR, VIEWS_SQL

# --------------------------------------------------------------------------
# B データ班のテーブル（CSVと同じ名前・同じ列）
# --------------------------------------------------------------------------
LOSS_TABLES = {
    # マスタ系
    "M_STORE": """
        CREATE TABLE IF NOT EXISTS M_STORE (
            store_cd TEXT PRIMARY KEY,
            store_name TEXT NOT NULL,
            trade_area_type TEXT NOT NULL,
            address TEXT
        )
    """,
    "M_PRODUCT": """
        CREATE TABLE IF NOT EXISTS M_PRODUCT (
            product_cd TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            cost_price INTEGER NOT NULL,
            sales_price INTEGER NOT NULL,
            shelf_life_days INTEGER NOT NULL
        )
    """,
    "M_WEATHER": """
        CREATE TABLE IF NOT EXISTS M_WEATHER (
            date TEXT NOT NULL,
            region_cd TEXT NOT NULL,
            weather TEXT NOT NULL,
            max_temp REAL NOT NULL,
            min_temp REAL NOT NULL,
            temp_diff_prev_day REAL NOT NULL,
            PRIMARY KEY (date, region_cd)
        )
    """,
    # トランザクション系
    "T_SALES": """
        CREATE TABLE IF NOT EXISTS T_SALES (
            sales_id TEXT PRIMARY KEY,
            sales_date TEXT NOT NULL,
            store_cd TEXT NOT NULL,
            product_cd TEXT NOT NULL,
            qty INTEGER NOT NULL,
            amount INTEGER NOT NULL
        )
    """,
    "T_ORDER": """
        CREATE TABLE IF NOT EXISTS T_ORDER (
            order_id TEXT PRIMARY KEY,
            order_date TEXT NOT NULL,
            delivery_date TEXT NOT NULL,
            store_cd TEXT NOT NULL,
            product_cd TEXT NOT NULL,
            ai_recommended_qty INTEGER NOT NULL,
            final_order_qty INTEGER NOT NULL,
            manager_memo TEXT
        )
    """,
    "T_INVENTORY": """
        CREATE TABLE IF NOT EXISTS T_INVENTORY (
            date TEXT NOT NULL,
            store_cd TEXT NOT NULL,
            product_cd TEXT NOT NULL,
            closing_stock_qty INTEGER NOT NULL,
            out_of_stock_hours REAL NOT NULL,
            lost_sales_est_amount INTEGER NOT NULL,
            PRIMARY KEY (date, store_cd, product_cd)
        )
    """,
    "T_WASTE_DISCOUNT": """
        CREATE TABLE IF NOT EXISTS T_WASTE_DISCOUNT (
            date TEXT NOT NULL,
            store_cd TEXT NOT NULL,
            product_cd TEXT NOT NULL,
            waste_qty INTEGER NOT NULL,
            discount_qty INTEGER NOT NULL,
            discount_amount INTEGER NOT NULL,
            PRIMARY KEY (date, store_cd, product_cd)
        )
    """,
}
LOSS_VIEWS = ("V_STORE_LOSS_SUMMARY", "V_WEATHER_HYPOTHESIS_CHECK", "V_EVENING_DISCOUNT_CANDIDATES")

# --------------------------------------------------------------------------
# C クローラーが取得した結果の保存先（lib/crawl_store.py が読み書きする）
# --------------------------------------------------------------------------
CRAWL_TABLES = {
    "crawl_flyers": """
        CREATE TABLE IF NOT EXISTS crawl_flyers (
            store_name TEXT NOT NULL,
            title TEXT,
            period_text TEXT,
            period_start TEXT,
            period_end TEXT,
            image_url TEXT,
            detail_url TEXT,
            leaflet_id TEXT,
            image_width INTEGER,
            image_height INTEGER,
            fetched_at TEXT NOT NULL
        )
    """,
    "crawl_featured_items": """
        CREATE TABLE IF NOT EXISTS crawl_featured_items (
            store_name TEXT NOT NULL,
            sale_date TEXT,
            product_name TEXT NOT NULL,
            spec TEXT,
            base_price INTEGER,
            tax_price INTEGER,
            is_featured INTEGER,
            image_url TEXT,
            product_url TEXT,
            fetched_at TEXT NOT NULL
        )
    """,
    "crawl_events": """
        CREATE TABLE IF NOT EXISTS crawl_events (
            title TEXT NOT NULL,
            period_raw TEXT,
            start_date TEXT,
            end_date TEXT,
            venue TEXT,
            category TEXT,
            description TEXT,
            image_url TEXT,
            url TEXT,
            fetched_at TEXT NOT NULL
        )
    """,
}

_done: set[str] = set()   # このプロセスで統合済みのDBファイル


def _objects(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}


def _import_csv(con: sqlite3.Connection, table: str) -> int:
    """data/csv/<テーブル名>.csv を取り込む（同じキーの行は上書き）。"""
    path = CSV_DIR / f"{table}.csv"
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [r for r in reader if r]
    cols = ", ".join(header)
    marks = ", ".join("?" * len(header))
    con.executemany(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({marks})", rows)
    return len(rows)


def load_loss_tables(con: sqlite3.Connection) -> dict[str, int]:
    """データ班の7テーブルをCSVから作り直し、分析ビュー3本を作成する。"""
    counts = {}
    for table, ddl in LOSS_TABLES.items():
        con.execute(f"DROP TABLE IF EXISTS {table}")
        con.execute(ddl)
        counts[table] = _import_csv(con, table)
    con.executescript(VIEWS_SQL.read_text(encoding="utf-8"))
    return counts


def ensure(db_path: str | Path, force: bool = False) -> None:
    """DBファイルに、データ班のテーブル・ビューとクローラー用テーブルを用意する。

    force=True のときは、CSVを読み直してデータ班のテーブルを作り直す（CSVを更新したとき用）。
    """
    key = str(db_path)
    if key in _done and not force:
        return
    con = sqlite3.connect(key, timeout=30)
    try:
        have = _objects(con)
        if force or not (set(LOSS_TABLES) | set(LOSS_VIEWS)) <= have:
            load_loss_tables(con)
        for ddl in CRAWL_TABLES.values():
            con.execute(ddl)
        con.commit()
    finally:
        con.close()
    _done.add(key)
