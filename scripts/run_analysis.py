import os
import sqlite3
import sys

# config.py からパスを取得できるようにパスを追加
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import BASE_DIR, DB_PATH

# 1. データベース接続
conn = sqlite3.connect(DB_PATH)
sql_file_path = os.path.join(BASE_DIR, "scripts", "analysis_views.sql")

print("=== SQL ビューの作成実行 ===")
# analysis_views.sql を読み込んで実行（一括でビューを作成）
with open(sql_file_path, "r", encoding="utf-8") as f:
    sql_script = f.read()

conn.executescript(sql_script)
print("ビュー作成完了: V_STORE_LOSS_SUMMARY, V_WEATHER_HYPOTHESIS_CHECK, V_EVENING_DISCOUNT_CANDIDATES\n")

# 2. 集計結果の出力（pandasが利用可能な場合は整形して表示、ない場合は標準フォーマットで表示）
try:
    import pandas as pd
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 1000)

    print("=" * 80)
    print("【分析①】店舗別 3大ロスおよび粗利インパクト集計 (V_STORE_LOSS_SUMMARY)")
    print("=" * 80)
    df_summary = pd.read_sql_query("SELECT * FROM V_STORE_LOSS_SUMMARY", conn)
    print(df_summary)

    print("\n" + "=" * 80)
    print("【分析②】気温急変日の仮説検証（AI推奨 vs 店長据え置きの機会ロス差）")
    print("=" * 80)
    query_weather = """
    SELECT date, weather, temp_diff_prev_day, store_name, product_name, 
           ai_recommended_qty, final_order_qty, manager_memo, out_of_stock_hours, opportunity_loss
    FROM V_WEATHER_HYPOTHESIS_CHECK
    LIMIT 8;
    """
    df_weather = pd.read_sql_query(query_weather, conn)
    print(df_weather)

except ImportError:
    # pandas未インストール時のフォールバック表示
    cur = conn.cursor()
    print("=" * 80)
    print("【分析①】店舗別 3大ロスおよび粗利インパクト集計 (V_STORE_LOSS_SUMMARY)")
    print("=" * 80)
    cur.execute("SELECT store_cd, store_name, trade_area_type, total_sales_amount, estimated_gross_profit, total_loss_amount FROM V_STORE_LOSS_SUMMARY")
    rows = cur.fetchall()
    print(f"{'店舗CD':<8}{'店舗名':<16}{'商圏タイプ':<14}{'売上総額':<10}{'推定粗利':<10}{'3大ロス合計':<10}")
    print("-" * 70)
    for r in rows:
        print(f"{r[0]:<8}{r[1]:<16}{r[2]:<14}{r[3]:<10}{r[4]:<10}{r[5]:<10}")

    print("\n" + "=" * 80)
    print("【分析②】気温急変日の仮説検証（抜粋）")
    print("=" * 80)
    cur.execute("""
        SELECT date, weather, temp_diff_prev_day, store_name, product_name, 
               ai_recommended_qty, final_order_qty, opportunity_loss
        FROM V_WEATHER_HYPOTHESIS_CHECK LIMIT 5
    """)
    for r in cur.fetchall():
        print(r)

conn.close()
print("\n=== 分析完了 ===")