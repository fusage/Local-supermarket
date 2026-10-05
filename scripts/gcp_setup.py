# -*- coding: utf-8 -*-
"""BigQuery の中身（データセット・テーブル・ビュー）を作るスクリプト。何度実行しても同じ結果になります。

    python scripts/gcp_setup.py --project <プロジェクトID> --bucket <バケット名>
    python scripts/gcp_setup.py --project <プロジェクトID> --bucket <バケット名> --csv-only
        … データ班のCSV（data/csv）を更新したとき。M_* / T_* と分析ビューだけ入れ直す

事前に、手元で `gcloud auth application-default login` を済ませておきます（手順は docs/gcp.md）。

作るもの（データセット sunfuji）
  外部テーブル raw_<種類>   … Cloud Storage の gs://<バケット>/raw/<種類>/*.csv をそのまま読む（生データ）
  ビュー crawl_*            … 上に「どのファイルから来たか」（source_file）を足したもの（履歴）
  ビュー crawl_*_latest     … 最新の1回ぶん（画面用）。SQLite版（lib/integrate.py の CRAWL_VIEWS）と同じ中身
  ビュー _ingest_log        … 取り込まれている生データファイルの一覧
  テーブル M_* / T_*        … データ班のCSV（data/csv）
  ビュー V_*                … データ班の分析ビュー（scripts/analysis_views.sql を BigQuery 用に変換）
"""
import argparse
import csv
import io
import os
import re
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")   # Windowsのコンソールでの文字化け対策

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--project", required=True, help="GCPのプロジェクトID")
parser.add_argument("--bucket", required=True, help="生データを置く Cloud Storage のバケット名")
parser.add_argument("--dataset", default="sunfuji", help="BigQuery のデータセット名（既定: sunfuji）")
parser.add_argument("--location", default="asia-northeast1", help="場所（既定: asia-northeast1＝東京）")
parser.add_argument("--csv-only", action="store_true", help="データ班のテーブルとビューだけ入れ直す")
args = parser.parse_args()

os.environ.update(GCP_PROJECT=args.project, GCP_BUCKET=args.bucket,
                  BQ_DATASET=args.dataset, GCP_LOCATION=args.location)

import pandas as pd  # noqa: E402
from google.cloud import bigquery  # noqa: E402

from lib import gcp  # noqa: E402
from lib.collect import SCHEMAS  # noqa: E402
from lib.integrate import RAW_SOURCES  # noqa: E402
from lib.paths import CSV_DIR, VIEWS_SQL  # noqa: E402

T = gcp.table

# 生データの列の型（ここに無い列は文字列。日付も、SQLite版と同じく 'YYYY-MM-DD' の文字列で持つ）
COL_TYPES = {
    "image_width": "INT64", "image_height": "INT64",
    "base_price": "INT64", "tax_price": "INT64", "is_featured": "INT64",
    "weather_code": "INT64", "is_forecast": "INT64",
    "max_temp": "FLOAT64", "min_temp": "FLOAT64", "precipitation": "FLOAT64",
    "quantity_kg": "FLOAT64", "price_per_kg": "FLOAT64", "quantity_ratio": "FLOAT64", "price_ratio": "FLOAT64",
}

# 最新の1回ぶん（SQLite版 lib/integrate.py の CRAWL_VIEWS と同じ考え方を BigQuery の書き方で）
LATEST_VIEWS = {
    "crawl_flyers_latest": """
        SELECT * EXCEPT(latest) FROM (
          SELECT *, MAX(fetched_at) OVER (PARTITION BY store_name) AS latest FROM {crawl_flyers})
        WHERE fetched_at = latest""",
    "crawl_featured_items_latest": """
        SELECT * EXCEPT(latest) FROM (
          SELECT *, MAX(fetched_at) OVER (PARTITION BY store_name) AS latest FROM {crawl_featured_items})
        WHERE fetched_at = latest""",
    "crawl_events_latest": """
        SELECT * EXCEPT(latest) FROM (
          SELECT *, MAX(fetched_at) OVER () AS latest FROM {crawl_events})
        WHERE fetched_at = latest""",
    "crawl_weather_latest": """
        SELECT date, region_cd, region_name, weather, weather_code, max_temp, min_temp,
               precipitation, is_forecast, fetched_at,
               ROUND(max_temp - LAG(max_temp) OVER (PARTITION BY region_cd ORDER BY date), 1)
                   AS temp_diff_prev_day
        FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY date, region_cd ORDER BY fetched_at DESC) AS rn
              FROM {crawl_weather})
        WHERE rn = 1""",
}


def step(msg: str) -> None:
    print(f"- {msg}")


def setup_dataset() -> None:
    ds = bigquery.Dataset(f"{args.project}.{args.dataset}")
    ds.location = args.location
    gcp.bq().create_dataset(ds, exists_ok=True)
    step(f"データセット {args.project}.{args.dataset}（{args.location}）")


def setup_raw() -> None:
    """生データの外部テーブルと、履歴・最新・取り込み記録のビュー。"""
    bucket = gcp.gcs().bucket(args.bucket)
    for src, view in RAW_SOURCES.items():
        cols = SCHEMAS[src][0]
        # 列の並びの見本（0行）。まだ1度も集めていない種類でも、ビューがエラーにならないようにする
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerow(cols)
        bucket.blob(f"raw/{src}/_schema.csv").upload_from_string(buf.getvalue(), content_type="text/csv")

        schema = ",\n  ".join(f"{c} {COL_TYPES.get(c, 'STRING')}" for c in cols)
        gcp.run(f"""
            CREATE OR REPLACE EXTERNAL TABLE {T('raw_' + src)} (
              {schema}
            ) OPTIONS (
              format = 'CSV',
              uris = ['gs://{args.bucket}/raw/{src}/*.csv'],
              skip_leading_rows = 1,
              allow_quoted_newlines = TRUE,
              encoding = 'UTF-8'
            )""")
        gcp.run(f"""
            CREATE OR REPLACE VIEW {T(view)} AS
            SELECT *, REGEXP_EXTRACT(_FILE_NAME, r'(raw/.+)$') AS source_file FROM {T('raw_' + src)}""")
        step(f"外部テーブル raw_{src} ← gs://{args.bucket}/raw/{src}/*.csv、ビュー {view}")

    names = {v: T(v) for v in RAW_SOURCES.values()}
    for view, sql in LATEST_VIEWS.items():
        gcp.run(f"CREATE OR REPLACE VIEW {T(view)} AS {sql.format(**names)}")
        step(f"ビュー {view}")

    union = "\nUNION ALL\n".join(
        f"SELECT source_file AS path, '{src}' AS source, COUNT(*) AS `rows`, "
        f"MAX(fetched_at) AS loaded_at FROM {T(view)} GROUP BY source_file"
        for src, view in RAW_SOURCES.items())
    gcp.run(f"CREATE OR REPLACE VIEW {T('_ingest_log')} AS {union}")
    step("ビュー _ingest_log")


def setup_loss() -> None:
    """データ班のテーブル（CSVをそのまま）と分析ビュー。"""
    from lib.integrate import LOSS_TABLES
    cfg = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    for name in LOSS_TABLES:
        path = CSV_DIR / f"{name}.csv"
        if not path.exists():
            step(f"{name}: data/csv/{name}.csv が無いので省きます")
            continue
        df = pd.read_csv(path, encoding="utf-8-sig")
        # 日付・コードは文字列のまま（SQLite版と同じ形にして、画面のコードを変えずに済ませる）
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].astype("string")
        gcp.bq().load_table_from_dataframe(df, f"{args.project}.{args.dataset}.{name}", job_config=cfg).result()
        step(f"テーブル {name}（{len(df):,}行）")

    # scripts/analysis_views.sql（SQLite用）を BigQuery 用に変換して作る
    sql = VIEWS_SQL.read_text(encoding="utf-8")
    sql = re.sub(r"(?im)^\s*DROP VIEW IF EXISTS\s+\w+\s*;\s*$", "", sql)
    for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
        m = re.search(r"CREATE VIEW\s+(\w+)\s+AS", stmt, re.I)
        if not m:
            continue
        body = stmt[m.end():]
        for name in LOSS_TABLES:    # ビューの中のテーブル名は、データセットまで書いておく必要がある
            body = re.sub(rf"\b{name}\b", T(name), body)
        gcp.run(f"CREATE OR REPLACE VIEW {T(m.group(1))} AS {body}")
        step(f"ビュー {m.group(1)}")


def main() -> None:
    print(f"プロジェクト {args.project} / バケット {args.bucket} / データセット {args.dataset}")
    setup_dataset()
    if not args.csv_only:
        setup_raw()
    setup_loss()
    print("\n完了しました。BigQuery のコンソールで、データセットの中身を確認できます。")


main()
