# -*- coding: utf-8 -*-
"""ファイルの置き場所（ここ1か所を直せば、画面・スクリプトの両方に反映されます）

【DBファイルの探し方】
1. 環境変数 SUPERMARKET_DB が指すファイル
2. data/supermarket.db        ← 藤井さん担当の本番ダミーデータを置く場所
3. data/supermarket_dummy.db  ← 上が無いとき、lib/sample_data.py が自動生成する暫定データ
"""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = APP_DIR / "data"
CSV_DIR = DATA_DIR / "csv"                       # データ班のCSV（M_* / T_*）
VIEWS_SQL = APP_DIR / "scripts" / "analysis_views.sql"
REAL_DB = DATA_DIR / "supermarket.db"
DUMMY_DB = DATA_DIR / "supermarket_dummy.db"


def resolve_db_path() -> tuple[Path, str]:
    """使用するDBファイルと、その種別（real / dummy）を返す。"""
    env = os.environ.get("SUPERMARKET_DB")
    if env and Path(env).exists():
        return Path(env), "real"
    if REAL_DB.exists():
        return REAL_DB, "real"
    return DUMMY_DB, "dummy"
