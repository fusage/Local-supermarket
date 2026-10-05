# -*- coding: utf-8 -*-
"""クローラーの取得結果をDBに保存し、読み出す。

取得結果は履歴として貯めていきます（テーブルは lib/integrate.py の CRAWL_TABLES）。
  ・毎朝の自動収集（scripts/collect.py）… data/raw/ に生データを書き、それをDBに取り込む
  ・画面からの「今すぐ取得」             … DBに直接保存（source_file='live'。店舗ごとに直近1回ぶん）
画面には、最新の1回ぶんだけを返す *_latest ビューから読み出します。

GCPの設定があるとき（lib/gcp.py）は、BigQuery の同じ名前のビューから読みます。
このとき画面からは保存しません（BigQuery は読むだけ。貯めるのは毎朝の Cloud Run ジョブ）。
サイト側の都合で取得に失敗したときは、前回保存したぶんを画面に出せます。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd

from lib import gcp
from lib.paths import resolve_db_path

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))     # GitHub Actions はUTCで動くので、日本時間に固定する
LIVE = "live"                          # 画面から直接取得したぶんの source_file

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
FEATURED_COLS = ["store_name", *FEATURED_MAP, "fetched_at"]
EVENT_COLS = [*EVENT_MAP, "fetched_at"]


def now() -> str:
    """取得日時（日本時間）。1回の取得で取ったものは、すべて同じ値にそろえる。"""
    return datetime.now(JST).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")


def _connect() -> sqlite3.Connection:
    path, _ = resolve_db_path()
    return sqlite3.connect(str(path), timeout=30)


def _read(sql: str, params: tuple = ()) -> pd.DataFrame:
    """キャッシュせずに読む（保存した直後の内容をそのまま画面に出すため）。

    BigQuery のときは5分間キャッシュする（画面からは保存しないので、古くならない）。
    SQLは SQLite と BigQuery の両方で動く書き方にしてある。
    """
    if gcp.enabled():
        try:
            return gcp.query(sql, params)
        except Exception as e:  # noqa: BLE001  ビューがまだ無い・まだ1度も集めていないなど
            logger.warning("BigQuery の読み出しに失敗: %s", e)
            return pd.DataFrame()
    con = _connect()
    try:
        return pd.read_sql_query(sql, con, params=params)
    except Exception:          # テーブルがまだ無いなど
        return pd.DataFrame()
    finally:
        con.close()


# --------------------------------------------------------------------------
# クローラーの戻り値 → 保存する行（生データのCSVとDBで同じ形）
# --------------------------------------------------------------------------
def flyer_rows(store_name: str, flyers: list[dict], fetched_at: str) -> list[dict]:
    rows = []
    for f in flyers:
        row = {c: f.get(c) for c in FLYER_COLS}
        row["store_name"] = store_name
        row["fetched_at"] = fetched_at
        rows.append(row)
    return rows


def featured_rows(store_name: str, items: list[dict], fetched_at: str) -> list[dict]:
    return [
        {"store_name": store_name,
         **{dst: int(bool(it.get(src))) if dst == "is_featured" else it.get(src)
            for dst, src in FEATURED_MAP.items()},
         "fetched_at": fetched_at}
        for it in items
    ]


def event_rows(records: list[dict], fetched_at: str) -> list[dict]:
    return [{**{dst: r.get(src) for dst, src in EVENT_MAP.items()}, "fetched_at": fetched_at}
            for r in records]


# --------------------------------------------------------------------------
# 保存（画面からの「今すぐ取得」）
# --------------------------------------------------------------------------
def _save_live(table: str, cols: list[str], rows: list[dict], store_name: str | None = None) -> None:
    """画面から取得したぶんを保存する。前回の 'live' 行は置き換える（履歴は毎朝の生データで持つ）。"""
    if gcp.enabled():          # BigQuery には画面から書き込まない
        return
    con = _connect()
    try:
        if store_name is None:
            con.execute(f"DELETE FROM {table} WHERE source_file=?", (LIVE,))
        else:
            con.execute(f"DELETE FROM {table} WHERE source_file=? AND store_name=?", (LIVE, store_name))
        all_cols = [*cols, "source_file"]
        con.executemany(
            f"INSERT INTO {table} ({', '.join(all_cols)}) VALUES ({', '.join('?' * len(all_cols))})",
            [(*[r.get(c) for c in cols], LIVE) for r in rows])
        con.commit()
    finally:
        con.close()


def save_flyers(store_name: str, flyers: list[dict]) -> None:
    _save_live("crawl_flyers", FLYER_COLS, flyer_rows(store_name, flyers, now()), store_name)


def save_featured_items(store_name: str, items: list[dict]) -> None:
    _save_live("crawl_featured_items", FEATURED_COLS, featured_rows(store_name, items, now()), store_name)


def save_events(records: list[dict]) -> None:
    _save_live("crawl_events", EVENT_COLS, event_rows(records, now()))


# --------------------------------------------------------------------------
# 読み出し（クローラーが返す形に戻す）
# --------------------------------------------------------------------------
def load_flyers(store_name: str) -> list[dict]:
    df = _read("SELECT * FROM crawl_flyers_latest WHERE store_name=?", (store_name,))
    return df.astype(object).where(df.notna(), None).to_dict("records")


def load_featured_items(store_name: str | None = None) -> pd.DataFrame:
    """保存済みの特売商品（最新の1回ぶん）。列名は保存時のまま（store_name, sale_date, product_name, …）。"""
    if store_name:
        return _read("SELECT * FROM crawl_featured_items_latest WHERE store_name=? "
                     "ORDER BY sale_date, product_name", (store_name,))
    return _read("SELECT * FROM crawl_featured_items_latest ORDER BY store_name, sale_date, product_name")


def featured_as_crawler_records(store_name: str) -> list[dict]:
    df = load_featured_items(store_name)
    if df.empty:
        return []
    out = df.rename(columns=FEATURED_MAP)[list(FEATURED_MAP.values())].copy()
    out["イチオシ"] = out["イチオシ"].fillna(0).astype(bool)
    return out.astype(object).where(out.notna(), None).to_dict("records")


def load_events() -> tuple[list[dict], str | None]:
    """保存済みのイベント（最新の1回ぶん）と、その取得日時を返す。"""
    df = _read("SELECT * FROM crawl_events_latest ORDER BY start_date, title")
    if df.empty:
        return [], None
    fetched = str(df["fetched_at"].max())
    out = df.rename(columns=EVENT_MAP)[list(EVENT_MAP.values())]
    return out.astype(object).where(out.notna(), None).to_dict("records"), fetched


def load_weather(region_cd: str | None = None) -> pd.DataFrame:
    """日ごとの天気（同じ日を何度も取得していれば、一番新しい取得）。"""
    if region_cd:
        return _read("SELECT * FROM crawl_weather_latest WHERE region_cd=? ORDER BY date", (region_cd,))
    return _read("SELECT * FROM crawl_weather_latest ORDER BY region_cd, date")


def load_market(item: str | None = None) -> pd.DataFrame:
    """卸売相場（取引日・品目ごとの数量と1kgあたりの価格）。"""
    if item:
        return _read("SELECT * FROM crawl_market WHERE item=? ORDER BY date", (item,))
    return _read("SELECT * FROM crawl_market ORDER BY date, category, item")


def last_fetched(table: str, store_name: str | None = None) -> str | None:
    if table not in ("crawl_flyers", "crawl_featured_items", "crawl_events", "crawl_weather", "crawl_market"):
        raise ValueError(table)
    if store_name:
        df = _read(f"SELECT MAX(fetched_at) t FROM {table} WHERE store_name=?", (store_name,))
    else:
        df = _read(f"SELECT MAX(fetched_at) t FROM {table}")
    if df.empty or df["t"][0] is None:
        return None
    return str(df["t"][0])


# --------------------------------------------------------------------------
# データ基盤の状態（views/pipeline.py 用）
# --------------------------------------------------------------------------
def ingest_log() -> pd.DataFrame:
    """取り込んだ生データファイルの一覧（新しい順）。"""
    return _read("SELECT path, source, `rows`, loaded_at FROM _ingest_log ORDER BY path DESC")


def history_counts(table: str) -> pd.DataFrame:
    """取得日ごとの件数（日別の推移を見る用）。"""
    if table not in ("crawl_flyers", "crawl_featured_items", "crawl_events", "crawl_weather", "crawl_market"):
        raise ValueError(table)
    return _read(f"SELECT substr(fetched_at, 1, 10) AS fetched_date, COUNT(*) AS `rows`, "
                 f"MAX(fetched_at) AS fetched_at FROM {table} GROUP BY fetched_date ORDER BY fetched_date")


def featured_price_history() -> pd.DataFrame:
    """競合の特売商品を、取得日をまたいで並べたもの（同じ商品の価格推移を見る用）。"""
    return _read("SELECT store_name, product_name, spec, sale_date, tax_price, base_price, "
                 "substr(fetched_at, 1, 10) AS fetched_date FROM crawl_featured_items "
                 "ORDER BY store_name, product_name, sale_date")
