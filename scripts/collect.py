# -*- coding: utf-8 -*-
"""外部データを集めて、生データ（data/raw/<種類>/<日付>.csv）に書き出すスクリプト

    python scripts/collect.py                        天気・イベント・競合チラシを全部集める
    python scripts/collect.py --only weather         天気だけ（カンマ区切りで複数可: weather,events）
    python scripts/collect.py --only weather --load  集めたあと、手元のDBにも取り込む
    python scripts/collect.py --only market --market-days 60
                                                     卸売相場を、まだ取っていない取引日から60日ぶん取る（初回用）

毎朝 GitHub Actions（.github/workflows/collect.yml）がこれを実行し、増えたファイルをコミットします。
DBには触らない（--load を付けたときだけ取り込む）ので、アプリを起動したままでも実行できます。
どれか1つでも失敗したら終了コード1で終わります（Actions のメール通知で気づけます）。
"""
import argparse
import logging
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import collect, integrate  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")   # Windowsのコンソールでの文字化け対策

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--only", default=",".join(collect.JOBS),
                    help=f"集める種類（カンマ区切り）。既定: {','.join(collect.JOBS)}")
parser.add_argument("--load", action="store_true", help="集めたあと、手元のDBにも取り込む")
parser.add_argument("--market-days", type=int, default=10,
                    help="卸売相場を、まだ取っていない取引日から最大何日ぶん取るか（既定: 10）")
args = parser.parse_args()

jobs = tuple(j.strip() for j in args.only.split(",") if j.strip())
unknown = set(jobs) - set(collect.JOBS)
if unknown:
    sys.exit(f"知らない種類です: {', '.join(sorted(unknown))}（使えるのは {', '.join(collect.JOBS)}）")

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s", force=True)
result = collect.collect(jobs, market_days=args.market_days)

if args.load:
    path, _, missing = integrate.plan()
    if missing:
        print("DBがまだ無いので取り込みは省きます（アプリの初回起動か scripts/build_db.py で作られます）")
    else:
        loaded = integrate.ensure(path)
        print(f"DBに取り込み: {len(loaded)}ファイル {sum(loaded.values())}行")

failed = [k for k, v in result.items() if v.startswith("NG")]
if failed:
    print(f"\n失敗: {', '.join(failed)}")
    sys.exit(1)
print("\nすべて成功しました")
