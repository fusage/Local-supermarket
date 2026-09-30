# -*- coding: utf-8 -*-
"""統合DBを手元で作る／作り直すスクリプト（アプリを起動せずに実行できます）

    python scripts/build_db.py           足りないものだけ作る
    python scripts/build_db.py --csv     data/csv/*.csv を読み直す（CSVを更新したとき）
    python scripts/build_db.py --all     暫定ダミーデータから全部作り直す（30秒ほど）

※ アプリを起動したままだと --all は失敗します（Windowsではファイルが使用中になるため）。
"""
import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import integrate, sample_data  # noqa: E402
from lib.paths import resolve_db_path  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")   # Windowsのコンソールでの文字化け対策

path, kind = resolve_db_path()
print(f"DBファイル: {path}（{'本番データ' if kind == 'real' else '暫定ダミーデータ'}）")

if kind == "dummy" and ("--all" in sys.argv or not path.exists()):
    sample_data.generate(path, progress=lambda m, p: print(f"[{p:>4.0%}] {m}"))

integrate.ensure(path, force="--csv" in sys.argv or "--all" in sys.argv)

con = sqlite3.connect(path)
print("\n=== テーブルと件数 ===")
for name, typ in con.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY type, name"):
    n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    print(f"{typ:<5} {name:<32} {n:>10,} 件")
con.close()
