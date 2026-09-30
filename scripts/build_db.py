# -*- coding: utf-8 -*-
"""統合DBを手元で作る／作り直すスクリプト（アプリを起動せずに実行できます）

    python scripts/build_db.py           DBが無ければ作る。あれば足りないテーブルだけ足す
    python scripts/build_db.py --all     data/csv/*.csv から全部作り直す（CSVを更新したとき）
    python scripts/build_db.py --csv     データ班のテーブル（M_* / T_*）だけ読み直す
                                         ※ 画面用のテーブルは変わりません。ふつうは --all を使います

データ班のCSVがそろっていれば本番DB（data/supermarket.db）、無ければ暫定ダミーDBを作ります。
※ アプリを起動したままだと --all は失敗します（Windowsではファイルが使用中になるため）。
"""
import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import integrate  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")   # Windowsのコンソールでの文字化け対策

path, kind, missing = integrate.plan()
print(f"DBファイル: {path}（{'データ班のCSV＋補完' if kind == 'real' else '暫定ダミーデータ'}）")

if missing or "--all" in sys.argv:
    if not missing:
        con = sqlite3.connect(path)
        ours = con.execute("SELECT 1 FROM sqlite_master WHERE name='db_meta'").fetchone() or kind == "dummy"
        con.close()
        if not ours:        # 手で置いたDBを、うっかり上書きしない
            sys.exit("このDBは自動で組み立てたものではないため、上書きしません。"
                     "作り直す場合は、ファイルを別の場所へ移してから実行してください。")
    integrate.build(path, kind, progress=lambda m, p: print(f"[{p:>4.0%}] {m}"))

integrate.ensure(path, force="--csv" in sys.argv)

con = sqlite3.connect(path)
print("\n=== テーブルと件数 ===")
for name, typ in con.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY type, name"):
    n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    print(f"{typ:<5} {name:<32} {n:>10,} 件")
con.close()
