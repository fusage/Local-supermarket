# -*- coding: utf-8 -*-
"""クローラーの取得結果をDBに保存し、読み出す。

取得に成功するたびに「最新の1回分」で置き換える(履歴は持たない)。
サイト側の都合で取得に失敗したときは、前回保存した分を画面に出すのに使う。
テーブルの定義は lib/integrate.py の CRAWL_TABLES にある。

クローラーが返す dict のキーは、ここの列名とそろえてある。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

import pandas as pd

from lib import gcp
from lib.paths import resolve_db_path

# 日本時間(JST)の定義
JST = timezone(timedelta(hours=9))

# テーブルごとに、保存する列(store_name と fetched_at はここで付け足す)
COLUMNS = {
    "crawl_flyers": ["title", "period_start", "period_end", "image_url", "detail_url", "leaflet_id"],
    "crawl_featured_items": ["sale_date", "product_name", "spec", "base_price", "tax_price",
                             "is_featured", "image_url", "product_url"],
    "crawl_events": ["title", "period_raw", "start_date", "end_date", "venue", "category",
                     "description", "image_url", "url"],
}
ORDER_BY = {
    "crawl_flyers": "period_start",
    "crawl_featured_items": "sale_date, product_name",
    "crawl_events": "start_date, title",
}

# FEATURED_MAP・EVENT_MAPの定義（外部ファイルからのインポートの場合は適切な from ... import に変更してください）
FEATURED_MAP = ["sales", "customers", "inventory"] 
EVENT_MAP = ["title", "start_date", "end_date"]

FEATURED_COLS = ["store_name", *FEATURED_MAP, "fetched_at"]
EVENT_COLS = [*EVENT_MAP, "fetched_at"]


def now() -> str:
    """取得日時（日本時間）。1回の取得で取ったものは、すべて同じ値にそろえる。"""
    return datetime.now(JST).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")


def _connect() -> sqlite3.Connection:
    path, _ = resolve_db_path()
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row  # 読み出した行を dict のように扱えるようにする
    return con


def save(table: str, rows: List[dict], store_name: Optional[str] = None) -> None:
    """table の中身(store_name 指定時はその店舗の分だけ)を rows で置き換える。"""
    cols = COLUMNS[table] + ["fetched_at"]
    where, params = ("WHERE store_name=?", (store_name,)) if store_name else ("", ())
    if store_name:
        cols = ["store_name"] + cols
    fetched_now = now()
    values = [
        tuple(store_name if c == "store_name" else fetched_now if c == "fetched_at" else r.get(c) for c in cols)
        for r in rows
    ]
    con = _connect()
    try:
        with con:  # ブロックを抜けるとコミット(エラー時はロールバック)
            con.execute(f"DELETE FROM {table} {where}", params)
            con.executemany(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values)
    finally:
        con.close()


def load(table: str, store_name: Optional[str] = None) -> Tuple[List[dict], Optional[str]]:
    """保存済みの行と、その取得日時を返す。テーブルが無い・空のときは ([], None)。"""
    where, params = ("WHERE store_name=?", (store_name,)) if store_name else ("", ())
    con = _connect()
    try:
        rows = con.execute(f"SELECT * FROM {table} {where} ORDER BY {ORDER_BY[table]}", params).fetchall()
    except sqlite3.OperationalError:  # テーブルがまだ無い
        return [], None
    finally:
        con.close()
    if not rows:
        return [], None
    records = [{c: r[c] for c in COLUMNS[table]} for r in rows]
    return records, max(r["fetched_at"] for r in rows)


def load_featured_items() -> pd.DataFrame:
    """全店舗の特売商品を、store_name・fetched_at 列つきの表で返す(app.py のバイヤー画面が使う)。"""
    con = _connect()
    try:
        return pd.read_sql_query(
            f"SELECT * FROM crawl_featured_items ORDER BY store_name, {ORDER_BY['crawl_featured_items']}", con)
    except Exception:  # noqa: BLE001  テーブルがまだ無い
        return pd.DataFrame()
    finally:
        con.close()