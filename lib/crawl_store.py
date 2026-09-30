# -*- coding: utf-8 -*-
"""クローラーの取得結果をDBに保存し、読み出す。

取得に成功するたびに「最新の1回ぶん」で置き換えます（履歴は持ちません）。
サイト側の都合で取得に失敗したときは、前回保存したぶんを画面に出せます。
テーブルは lib/integrate.py の CRAWL_TABLES で定義しています。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pandas as pd

from lib.paths import resolve_db_path

FLYER_COLS = ["store_name", "title", "period_text", "period_start", "period_end",
              "image_url", "detail_url", "leaflet_id", "image_width", "image_height", "fetched_at"]
# 保存するときの列名 ← クローラーが返す列名
FEATURED_MAP = {
    "sale_date": "日付", "product_name": "商品名", "spec": "内容",
    "base_price": "本体価格", "tax_price": "税込価格", "is_featured": "イチオシ",
    "image_url": "画像URL", "product_url": "商品URL",
}
EVENT_MAP = {
    "title": "イベント名", "period_raw": "日時", "start_date": "開始日", "end_date": "終了日",
    "venue": "場所・会場", "category": "分野", "description": "説明",
    "image_url": "画像URL", "url": "URL",
}


def _connect() -> sqlite3.Connection:
    path, _ = resolve_db_path()
    return sqlite3.connect(str(path), timeout=30)


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _read(sql: str, params: tuple = ()) -> pd.DataFrame:
    """キャッシュせずに読む（保存した直後の内容をそのまま画面に出すため）。"""
    con = _connect()
    try:
        return pd.read_sql_query(sql, con, params=params)
    except Exception:          # テーブルがまだ無いなど
        return pd.DataFrame()
    finally:
        con.close()


# --------------------------------------------------------------------------
# 保存
# --------------------------------------------------------------------------
def save_flyers(store_name: str, flyers: list[dict]) -> None:
    con = _connect()
    try:
        con.execute("DELETE FROM crawl_flyers WHERE store_name=?", (store_name,))
        now = _now()
        rows = []
        for f in flyers:
            row = {c: f.get(c) for c in FLYER_COLS}
            row["store_name"] = store_name
            row["fetched_at"] = now
            rows.append(tuple(row[c] for c in FLYER_COLS))
        con.executemany(
            f"INSERT INTO crawl_flyers ({', '.join(FLYER_COLS)}) "
            f"VALUES ({', '.join('?' * len(FLYER_COLS))})", rows)
        con.commit()
    finally:
        con.close()


def save_featured_items(store_name: str, items: list[dict]) -> None:
    con = _connect()
    try:
        con.execute("DELETE FROM crawl_featured_items WHERE store_name=?", (store_name,))
        now = _now()
        cols = ["store_name", *FEATURED_MAP, "fetched_at"]
        rows = [
            (store_name,
             *[int(bool(it.get(src))) if dst == "is_featured" else it.get(src)
               for dst, src in FEATURED_MAP.items()],
             now)
            for it in items
        ]
        con.executemany(
            f"INSERT INTO crawl_featured_items ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})", rows)
        con.commit()
    finally:
        con.close()


def save_events(records: list[dict]) -> None:
    con = _connect()
    try:
        con.execute("DELETE FROM crawl_events")
        now = _now()
        cols = [*EVENT_MAP, "fetched_at"]
        rows = [(*[r.get(src) for src in EVENT_MAP.values()], now) for r in records]
        con.executemany(
            f"INSERT INTO crawl_events ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})", rows)
        con.commit()
    finally:
        con.close()


# --------------------------------------------------------------------------
# 読み出し（クローラーが返す形に戻す）
# --------------------------------------------------------------------------
def load_flyers(store_name: str) -> list[dict]:
    df = _read("SELECT * FROM crawl_flyers WHERE store_name=?", (store_name,))
    return df.astype(object).where(df.notna(), None).to_dict("records")


def load_featured_items(store_name: str | None = None) -> pd.DataFrame:
    """保存済みの特売商品。列名は保存時のまま（store_name, sale_date, product_name, …）。"""
    if store_name:
        return _read("SELECT * FROM crawl_featured_items WHERE store_name=? "
                     "ORDER BY sale_date, product_name", (store_name,))
    return _read("SELECT * FROM crawl_featured_items ORDER BY store_name, sale_date, product_name")


def featured_as_crawler_records(store_name: str) -> list[dict]:
    df = load_featured_items(store_name)
    if df.empty:
        return []
    out = df.rename(columns=FEATURED_MAP)[list(FEATURED_MAP.values())].copy()
    out["イチオシ"] = out["イチオシ"].fillna(0).astype(bool)
    return out.astype(object).where(out.notna(), None).to_dict("records")


def load_events() -> tuple[list[dict], str | None]:
    """保存済みのイベントと、その取得日時を返す。"""
    df = _read("SELECT * FROM crawl_events ORDER BY start_date, title")
    if df.empty:
        return [], None
    fetched = str(df["fetched_at"].max())
    out = df.rename(columns=EVENT_MAP)[list(EVENT_MAP.values())]
    return out.astype(object).where(out.notna(), None).to_dict("records"), fetched


def last_fetched(table: str, store_name: str | None = None) -> str | None:
    if table not in ("crawl_flyers", "crawl_featured_items", "crawl_events"):
        raise ValueError(table)
    if store_name:
        df = _read(f"SELECT MAX(fetched_at) t FROM {table} WHERE store_name=?", (store_name,))
    else:
        df = _read(f"SELECT MAX(fetched_at) t FROM {table}")
    if df.empty or df["t"][0] is None:
        return None
    return str(df["t"][0])
