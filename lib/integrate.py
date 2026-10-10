# -*- coding: utf-8 -*-
"""データの統合（3人の成果物を1つのSQLiteファイルにまとめる）

  A 画面が使うテーブル   … stores / products / sales など（ER図のテーブル）
  B データ班のテーブル   … M_STORE / T_SALES など7テーブル ＋ 分析ビュー3本
  C クローラーの取得結果 … crawl_flyers / crawl_featured_items / crawl_events / crawl_weather / crawl_market

Aは、データ班のCSV（data/csv/*.csv）があれば、それを正として組み立てます
（lib/real_data.py。足りない期間・商品・項目は補完）。CSVが無いときだけ、
暫定ダミーデータ（lib/sample_data.py）になります。
そのDBファイルに、BのCSVとCの保存用テーブルを足します。

Cは、毎朝 GitHub Actions が集めた生データ（data/raw/<種類>/<日付>.csv）を正とし、
まだ取り込んでいないファイルだけを追記します（取り込み済みの記録は _ingest_log）。
DBが消えても、生データから同じ履歴を作り直せます。

何度呼んでも同じ結果になる（足りないものだけ作る）ので、起動のたびに呼んで構いません。
このファイルは streamlit を読み込まないので、scripts/ からも使えます。
"""
from __future__ import annotations

import csv
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from lib import real_data, sample_data
from lib.paths import CSV_DIR, DATA_DIR, RAW_DIR, REAL_DB, VIEWS_SQL, resolve_db_path

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
#   取得のたびに行を足していく履歴テーブル。fetched_at が「いつ取得したか」、
#   source_file が「どの生データファイルから来たか」（画面から直接取得したぶんは 'live'）。
#   画面には、最新の1回ぶんだけを返す *_latest ビューを見せる。
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
            fetched_at TEXT NOT NULL,
            source_file TEXT NOT NULL
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
            fetched_at TEXT NOT NULL,
            source_file TEXT NOT NULL
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
            fetched_at TEXT NOT NULL,
            source_file TEXT NOT NULL
        )
    """,
    "crawl_market": """
        CREATE TABLE IF NOT EXISTS crawl_market (
            date TEXT NOT NULL,
            market TEXT NOT NULL,
            category TEXT NOT NULL,
            item TEXT NOT NULL,
            item_code TEXT,
            quantity_kg REAL,
            price_per_kg REAL,
            quantity_ratio REAL,
            price_ratio REAL,
            stat_inf_id TEXT,
            fetched_at TEXT NOT NULL,
            source_file TEXT NOT NULL
        )
    """,
    "crawl_weather": """
        CREATE TABLE IF NOT EXISTS crawl_weather (
            date TEXT NOT NULL,
            region_cd TEXT NOT NULL,
            region_name TEXT,
            weather TEXT,
            weather_code INTEGER,
            max_temp REAL,
            min_temp REAL,
            precipitation REAL,
            is_forecast INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            source_file TEXT NOT NULL
        )
    """,
}

# 最新の1回ぶんだけを返すビュー（画面はこちらを読む）
CRAWL_VIEWS = {
    "crawl_flyers_latest": """
        SELECT * FROM crawl_flyers f
        WHERE fetched_at = (SELECT MAX(fetched_at) FROM crawl_flyers g WHERE g.store_name = f.store_name)
    """,
    "crawl_featured_items_latest": """
        SELECT * FROM crawl_featured_items f
        WHERE fetched_at = (SELECT MAX(fetched_at) FROM crawl_featured_items g
                            WHERE g.store_name = f.store_name)
    """,
    "crawl_events_latest": """
        SELECT * FROM crawl_events WHERE fetched_at = (SELECT MAX(fetched_at) FROM crawl_events)
    """,
    # 同じ日の天気は何度も取得される（予報→実績）ので、日ごとに一番新しい取得を採用する。
    # temp_diff_prev_day は M_WEATHER と同じく「最高気温の前日差」。
    "crawl_weather_latest": """
        SELECT date, region_cd, region_name, weather, weather_code, max_temp, min_temp,
               precipitation, is_forecast, fetched_at,
               ROUND(max_temp - LAG(max_temp) OVER (PARTITION BY region_cd ORDER BY date), 1)
                   AS temp_diff_prev_day
        FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY date, region_cd ORDER BY fetched_at DESC) rn
              FROM crawl_weather)
        WHERE rn = 1
    """,
}

# 生データの種類（data/raw/<種類>/）→ 取り込み先のテーブル
RAW_SOURCES = {
    "flyers": "crawl_flyers",
    "featured": "crawl_featured_items",
    "events": "crawl_events",
    "weather": "crawl_weather",
    "market": "crawl_market",          # 卸売相場（取引日ごとに1ファイル）
}

INGEST_LOG = """
    CREATE TABLE IF NOT EXISTS _ingest_log (
        path TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        sha1 TEXT NOT NULL,
        rows INTEGER NOT NULL,
        loaded_at TEXT NOT NULL
    )
"""

_done: set[str] = set()   # このプロセスで統合済みのDBファイル
_raw_seen: dict[str, tuple] = {}   # DBファイルごとに、最後に取り込んだときの生データの状態


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


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def create_crawl_tables(con: sqlite3.Connection) -> None:
    """クローラー用の履歴テーブルと *_latest ビュー、取り込み記録を用意する。"""
    for table, ddl in CRAWL_TABLES.items():
        cols = _columns(con, table)
        if cols and "source_file" not in cols:
            # 履歴を持つ前の古い形（最新1回ぶんだけ）。中身は生データから入れ直せるので作り直す
            con.execute(f"DROP TABLE {table}")
        con.execute(ddl)
    for view, sql in CRAWL_VIEWS.items():
        con.execute(f"DROP VIEW IF EXISTS {view}")
        con.execute(f"CREATE VIEW {view} AS {sql}")
    con.execute(INGEST_LOG)


def _raw_files() -> list[tuple[str, Path]]:
    """取り込み対象の生データファイル（種類, パス）。"""
    return [(src, f) for src in RAW_SOURCES for f in sorted((RAW_DIR / src).glob("*.csv"))]


def _raw_fingerprint() -> tuple:
    """生データの状態（ファイル名・大きさ・更新時刻）。変わっていなければ取り込みを省く。"""
    out = []
    for _, f in _raw_files():
        s = f.stat()
        out.append((f.as_posix(), s.st_size, s.st_mtime_ns))
    return tuple(out)


def ingest_raw(con: sqlite3.Connection) -> dict[str, int]:
    """まだ取り込んでいない（または中身が変わった）生データファイルだけをDBに追記する。

    取り込み直すときは、そのファイルから来た行を消してから入れるので、
    何度呼んでも行が重複しない。戻り値は {ファイル: 行数}。
    """
    done = dict(con.execute("SELECT path, sha1 FROM _ingest_log"))
    loaded = {}
    for src, f in _raw_files():
        rel = f.relative_to(DATA_DIR).as_posix()        # 例: raw/weather/2026-10-01.csv
        body = f.read_bytes()
        sha = hashlib.sha1(body).hexdigest()
        if done.get(rel) == sha:
            continue
        table = RAW_SOURCES[src]
        reader = csv.reader(body.decode("utf-8-sig").splitlines())
        header = next(reader, None)
        if not header:
            continue
        have = _columns(con, table) - {"source_file"}
        idx = [i for i, c in enumerate(header) if c in have]
        cols = [header[i] for i in idx] + ["source_file"]
        rows = [tuple((r[i] if i < len(r) and r[i] != "" else None) for i in idx) + (rel,)
                for r in reader if r]
        con.execute(f"DELETE FROM {table} WHERE source_file=?", (rel,))
        con.executemany(f"INSERT INTO {table} ({', '.join(cols)}) "
                        f"VALUES ({', '.join('?' * len(cols))})", rows)
        con.execute("INSERT OR REPLACE INTO _ingest_log VALUES (?, ?, ?, ?, ?)",
                    (rel, src, sha, len(rows), datetime.now().isoformat(sep=" ", timespec="seconds")))
        loaded[rel] = len(rows)
    return loaded


def sync_raw(db_path: str | Path) -> dict[str, int]:
    """生データが増えていれば、そのぶんをDBに取り込む（増えていなければ何もしない）。"""
    key = str(db_path)
    fp = _raw_fingerprint()
    if _raw_seen.get(key) == fp:
        return {}
    con = sqlite3.connect(key, timeout=30)
    try:
        create_crawl_tables(con)
        loaded = ingest_raw(con)
        con.commit()
    finally:
        con.close()
    _raw_seen[key] = fp
    return loaded


def ensure(db_path: str | Path, force: bool = False) -> dict[str, int]:
    """DBファイルに、データ班のテーブル・ビューとクローラー用テーブルを用意する。

    force=True のときは、CSVを読み直してデータ班のテーブルを作り直す（CSVを更新したとき用）。
    生データ（data/raw）が増えていれば、そのぶんを取り込む（戻り値は取り込んだ {ファイル: 行数}）。
    """
    key = str(db_path)
    if key not in _done or force:
        con = sqlite3.connect(key, timeout=30)
        try:
            have = _objects(con)
            if force or not (set(LOSS_TABLES) | set(LOSS_VIEWS)) <= have:
                load_loss_tables(con)
            create_crawl_tables(con)
            con.commit()
        finally:
            con.close()
        _done.add(key)
    return sync_raw(db_path)


# --------------------------------------------------------------------------
# DBファイルそのものを作る
# --------------------------------------------------------------------------
def plan() -> tuple[Path, str, bool]:
    """使うDBファイル、その種別（real / dummy）、これから作る必要があるか、を返す。

    data/supermarket.db がすでにあれば（手で置いたものでも）そのまま使う。
    無ければ、データ班のCSVから本番DBを組み立てる。CSVも無ければ暫定ダミーデータ。
    """
    path, kind = resolve_db_path()
    if kind == "dummy" and real_data.available():
        path, kind = REAL_DB, "real"
    return path, kind, not path.exists()


def build(path: Path, kind: str, progress=None) -> None:
    """DBファイルを作る。作りかけを読まれないよう、別名で作ってから置き換える。"""
    tmp = path.with_name(path.name + ".building")
    generate = real_data.generate if kind == "real" else sample_data.generate
    generate(tmp, progress=progress)
    for key in (str(tmp), str(path)):      # 新しいファイルなので、統合・取り込みをやり直させる
        _done.discard(key)
        _raw_seen.pop(key, None)
    ensure(tmp)
    tmp.replace(path)
    _raw_seen[str(path)] = _raw_seen.pop(str(tmp), ())
    _done.discard(str(tmp))
    _done.add(str(path))
