# -*- coding: utf-8 -*-
"""GCP（Cloud Storage・BigQuery）とのやりとり

GCPの設定があるときだけ使います。無いときは今まで通り、手元の SQLite と data/raw で動きます。

【GCP版のデータの流れ】
  Cloud Scheduler（毎朝6時）→ Cloud Run ジョブ（scripts/collect.py）
    → Cloud Storage  gs://<バケット>/raw/<種類>/<日付>.csv   … 生データ（正）
    → BigQuery 外部テーブル raw_<種類>（上のCSVをそのまま読む）
    → BigQuery ビュー crawl_* / crawl_*_latest / _ingest_log  … 画面が読む
  データ班のデータ（M_* / T_* と分析ビュー V_*）も同じデータセットに入れます（scripts/gcp_setup.py）。

【設定の読み方】（上が優先）
  1. 環境変数   GCP_PROJECT / GCP_BUCKET / BQ_DATASET / GCP_LOCATION   … Cloud Run ジョブ・手元
  2. Streamlit の Secrets の [gcp] project / bucket / dataset / location … Streamlit Cloud
  認証は、Secrets に [gcp_service_account]（サービスアカウントの鍵）があればそれを、
  無ければ「アプリケーションのデフォルト認証情報」（Cloud Run のサービスアカウント、
  手元なら gcloud auth application-default login）を使います。

このファイルは streamlit が無くても動きます（Cloud Run ジョブ用）。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd

DEFAULT_DATASET = "sunfuji"
DEFAULT_LOCATION = "asia-northeast1"     # 東京。バケットとデータセットは同じ場所に置く（外部テーブルの条件）
CACHE_SECONDS = 300                      # 同じクエリは5分間使い回す（毎朝しか増えないデータなので十分）


# --------------------------------------------------------------------------
# 設定
# --------------------------------------------------------------------------
def _secrets() -> tuple[dict, dict | None]:
    """Streamlit の Secrets（無ければ空）。"""
    try:
        import streamlit as st
        gcp = dict(st.secrets["gcp"]) if "gcp" in st.secrets else {}
        key = dict(st.secrets["gcp_service_account"]) if "gcp_service_account" in st.secrets else None
        return gcp, key
    except Exception:          # streamlit が無い・secrets.toml が無い
        return {}, None


def settings() -> dict:
    sec, _ = _secrets()
    if sec and not os.environ.get("K_SERVICE"):
        # Streamlit Cloud・手元の画面では GCP のメタデータサーバーは無いので、探しに行かない
        # （探すと、鍵が無いときに1回12秒ほど待たされる）。Cloud Run・Cloud Shell には影響しない
        os.environ.setdefault("NO_GCE_CHECK", "true")
    return {
        "project": os.environ.get("GCP_PROJECT") or sec.get("project"),
        "bucket": os.environ.get("GCP_BUCKET") or sec.get("bucket"),
        "dataset": os.environ.get("BQ_DATASET") or sec.get("dataset") or DEFAULT_DATASET,
        "location": os.environ.get("GCP_LOCATION") or sec.get("location") or DEFAULT_LOCATION,
    }


def enabled() -> bool:
    """GCPの設定（プロジェクトとバケット）があるか。"""
    s = settings()
    return bool(s["project"] and s["bucket"])


_clients: dict = {}


def _credentials():
    _, key = _secrets()
    if key:
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_info(key)
    return None                # アプリケーションのデフォルト認証情報


def bq():
    s = settings()
    k = ("bq", s["project"])
    if k not in _clients:
        from google.cloud import bigquery
        _clients[k] = bigquery.Client(project=s["project"], credentials=_credentials(), location=s["location"])
    return _clients[k]


def gcs():
    s = settings()
    k = ("gcs", s["project"])
    if k not in _clients:
        from google.cloud import storage
        _clients[k] = storage.Client(project=s["project"], credentials=_credentials())
    return _clients[k]


def table(name: str) -> str:
    """`プロジェクト.データセット.テーブル`（DDLで使う完全な名前）。"""
    s = settings()
    return f"`{s['project']}.{s['dataset']}.{name}`"


# --------------------------------------------------------------------------
# Cloud Storage
# --------------------------------------------------------------------------
def upload_raw(local_path: Path, source: str) -> str:
    """生データのファイルを gs://<バケット>/raw/<種類>/<ファイル名> に置く（同じ日のファイルは置き換え）。"""
    s = settings()
    name = f"raw/{source}/{local_path.name}"
    gcs().bucket(s["bucket"]).blob(name).upload_from_filename(str(local_path), content_type="text/csv")
    return f"gs://{s['bucket']}/{name}"


def raw_exists(source: str, filename: str) -> bool:
    """gs://<バケット>/raw/<種類>/<ファイル名> がもうあるか（取り直しを省くため）。"""
    return gcs().bucket(settings()["bucket"]).blob(f"raw/{source}/{filename}").exists()


# --------------------------------------------------------------------------
# BigQuery
# --------------------------------------------------------------------------
_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}
RETRY_SECONDS = 300            # つながらなかったら、この間は BigQuery を試さない（すぐ SQLite に切り替える）
_down: dict = {"until": 0.0, "reason": None}


class Unavailable(Exception):
    """GCPにつながらない（認証情報が無い・鍵が無効・権限が無い・ネットワーク）。"""


def _is_connection_error(e: Exception) -> bool:
    from google.api_core import exceptions as api
    from google.auth import exceptions as auth
    return isinstance(e, (auth.GoogleAuthError, api.Unauthorized, api.Forbidden,
                          api.ServiceUnavailable, ConnectionError, OSError))


def status() -> str | None:
    """つながらない状態なら、その理由（画面に出す用）。つながる・まだ試していないなら None。"""
    return _down["reason"] if time.time() < _down["until"] else None


def query(sql: str, params: tuple = (), cache: bool = True) -> pd.DataFrame:
    """SQLを実行してDataFrameで返す。

    テーブル名はデータセットを省略して書ける（既定のデータセットにしてある）。
    パラメータは SQLite と同じ ? で渡せる（BigQuery の位置パラメータ）。
    つながらないときは Unavailable を出す（失敗から5分間は、試さずにすぐ出す）。
    """
    key = (sql, params)
    if cache and key in _cache and time.time() - _cache[key][0] < CACHE_SECONDS:
        return _cache[key][1].copy()
    if time.time() < _down["until"]:
        raise Unavailable(_down["reason"])
    from google.cloud import bigquery
    s = settings()
    types = {bool: "BOOL", int: "INT64", float: "FLOAT64"}
    cfg = bigquery.QueryJobConfig(
        default_dataset=f"{s['project']}.{s['dataset']}",
        query_parameters=[bigquery.ScalarQueryParameter(None, types.get(type(v), "STRING"), v)
                          for v in params])
    try:
        df = bq().query(sql, job_config=cfg).to_dataframe()
    except Exception as e:  # noqa: BLE001
        if _is_connection_error(e):
            _down.update(until=time.time() + RETRY_SECONDS, reason=f"{type(e).__name__}: {e}")
            raise Unavailable(_down["reason"]) from e
        raise                   # SQLの誤り・テーブルが無いなど
    _down.update(until=0.0, reason=None)
    _cache[key] = (time.time(), df)
    return df.copy()


def run(sql: str) -> None:
    """DDLなど、結果を返さないSQLを実行する。"""
    bq().query(sql).result()


def clear_cache() -> None:
    _cache.clear()
