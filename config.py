import os

# プロジェクトルート (retail-copilot/) の絶対パスを取得
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# フォルダ・DBパスの定義（変更が生じてもここ1箇所を直せばチーム全員に反映されます）
DATA_DIR = os.path.join(BASE_DIR, "data")
CSV_DIR = os.path.join(DATA_DIR, "csv")
DB_PATH = os.path.join(DATA_DIR, "retail.db")

# フォルダが存在しなければ自動生成
os.makedirs(CSV_DIR, exist_ok=True)