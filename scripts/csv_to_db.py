import csv
import os
import sqlite3
import sys

# config.py からパスを取得
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CSV_DIR, DB_PATH

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# 1. 全7テーブルのスキーマ定義（DDL）
tables_ddl = {
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

# テーブル作成実行
for tbl_name, ddl in tables_ddl.items():
    cur.execute(ddl)


# 2. CSV読み込み＆インポート関数
def import_csv(filename, table_name):
    filepath = os.path.join(CSV_DIR, filename)
    if not os.path.exists(filepath):
        print(f"スキップ（ファイルなし）: {filename}")
        return

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = [r for r in reader if r]

    placeholders = ", ".join(["?"] * len(header))
    sql = f"INSERT OR REPLACE INTO {table_name} VALUES ({placeholders})"
    cur.executemany(sql, rows)
    print(f"・{table_name}: {len(rows)} 件 登録完了")


print("=== 全テーブル SQLite インポート開始 ===")
import_csv("M_STORE.csv", "M_STORE")
import_csv("M_PRODUCT.csv", "M_PRODUCT")
import_csv("M_WEATHER.csv", "M_WEATHER")
import_csv("T_SALES.csv", "T_SALES")
import_csv("T_ORDER.csv", "T_ORDER")
import_csv("T_INVENTORY.csv", "T_INVENTORY")
import_csv("T_WASTE_DISCOUNT.csv", "T_WASTE_DISCOUNT")

conn.commit()
conn.close()
print(f"\n全データ同期完了！\nDBファイル: {DB_PATH}")