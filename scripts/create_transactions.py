import csv
import datetime
import os
import random
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CSV_DIR

# 再現性確保
random.seed(42)

# マスタ情報の簡易参照（先ほど登録したもの）
stores = ["ST001", "ST002", "ST003", "ST004", "ST005"]
products = [
    # (cd, category, sales_price, cost_price)
    ("SOZ001", "惣菜", 480, 240),
    ("SOZ002", "惣菜", 360, 180),
    ("SOZ003", "惣菜", 450, 220),
    ("SOZ004", "惣菜", 180, 90),
    ("NIP001", "日配", 260, 130),  # 鍋つゆ
    ("NIP002", "日配", 260, 130),  # 白湯鍋
    ("NIP003", "日配", 80, 40),  # 木綿豆腐
    ("NIP004", "日配", 70, 35),  # 絹豆腐
    ("NIP005", "日配", 140, 70),  # うどん
    ("SEI001", "生鮮", 320, 180),  # 豚バラ
    ("SEI002", "生鮮", 220, 120),  # 鶏もも
    ("SEI003", "生鮮", 180, 90),  # 鍋野菜
]

# 日付リスト（直近7日間）
dates = [
    ("2026-09-20", 0.5),
    ("2026-09-21", -2.5),
    ("2026-09-22", -4.0),  # 気温急減
    ("2026-09-23", -0.5),
    ("2026-09-24", 2.5),
    ("2026-09-25", 1.5),
    ("2026-09-26", -5.5),  # 気温急減
]

sales_rows = [["sales_id", "sales_date", "store_cd", "product_cd", "qty", "amount"]]
order_rows = [["order_id", "order_date", "delivery_date", "store_cd", "product_cd", "ai_recommended_qty", "final_order_qty", "manager_memo"]]
inventory_rows = [["date", "store_cd", "product_cd", "closing_stock_qty", "out_of_stock_hours", "lost_sales_est_amount"]]
waste_discount_rows = [["date", "store_cd", "product_cd", "waste_qty", "discount_qty", "discount_amount"]]

sales_id_seq = 1
order_id_seq = 1

for date_str, temp_diff in dates:
    # 前日発注日の設定
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    prev_date_str = (dt - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    for store_cd in stores:
        for p_cd, cat, price, cost in products:
            # 基準需要（日販ベース）
            base_demand = 30 if cat == "惣菜" else (40 if cat == "日配" else 25)

            # 気温急低下による鍋関連の特需係数
            is_nabe_item = p_cd in ["NIP001", "NIP002", "NIP003", "SEI001", "SEI003"]
            weather_factor = 1.0
            if is_nabe_item and temp_diff <= -3.0:
                weather_factor = 1.45  # 気温急減で需要1.45倍！

            demand = int(base_demand * weather_factor * random.uniform(0.85, 1.15))

            # AI推奨発注数（気温差を検知して適切に増減させる）
            ai_rec = int(base_demand * weather_factor)

            # 店長の発注判断シナリオ：
            # ST001（中央店）はAIの推奨を素直に採用、ST002（南店）は勘で前日並みに据え置き
            if store_cd == "ST001":
                final_order = ai_rec
                memo = "気温急変のAI提案を採用" if weather_factor > 1.2 else "通常発注"
            else:
                final_order = int(base_demand * random.uniform(0.9, 1.1))  # AIを無視して据え置き
                memo = "前週並みで据え置き" if weather_factor > 1.2 else "通常発注"

            # 発注データ登録
            order_rows.append([
                f"ORD{order_id_seq:06d}", prev_date_str, date_str, store_cd, p_cd, ai_rec, final_order, memo
            ])
            order_id_seq += 1

            # 販売数・在庫・欠品・廃棄のシミュレーション
            stock_available = final_order + random.randint(2, 5)  # 入荷＋前日残

            if demand > stock_available:
                # 【機会ロス（欠品）発生！】
                actual_sales = stock_available
                closing_stock = 0
                lost_qty = demand - stock_available
                lost_hours = min(6, round(lost_qty / (base_demand / 12), 1))
                lost_amount = lost_qty * price
                waste_qty = 0
                discount_qty = 0
                discount_amount = 0
            else:
                # 売り切り or 売れ残り
                actual_sales = demand
                closing_stock = stock_available - demand
                lost_hours = 0.0
                lost_amount = 0

                # 惣菜や生鮮は売れ残ると見切り・廃棄へ
                if cat in ["惣菜", "生鮮"] and closing_stock > 0:
                    discount_qty = int(closing_stock * 0.7)  # 7割は20〜50%引きで売り切り
                    discount_amount = int(discount_qty * (price * 0.3))  # 平均30%引き
                    waste_qty = closing_stock - discount_qty  # 残り3割は廃棄
                    closing_stock = 0
                else:
                    discount_qty = 0
                    discount_amount = 0
                    waste_qty = 0

            # 売上データ登録
            sales_amount = actual_sales * price
            sales_rows.append([f"SLS{sales_id_seq:06d}", date_str, store_cd, p_cd, actual_sales, sales_amount])
            sales_id_seq += 1

            # 在庫・機会ロス登録
            inventory_rows.append([date_str, store_cd, p_cd, closing_stock, lost_hours, lost_amount])

            # 廃棄・見切り登録
            waste_discount_rows.append([date_str, store_cd, p_cd, waste_qty, discount_qty, discount_amount])

# CSV保存
def write_csv(filename, rows):
    path = os.path.join(CSV_DIR, filename)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    print(f"・{filename}: {len(rows)-1} 件 生成")

print("=== トランザクションCSV生成開始 ===")
write_csv("T_SALES.csv", sales_rows)
write_csv("T_ORDER.csv", order_rows)
write_csv("T_INVENTORY.csv", inventory_rows)
write_csv("T_WASTE_DISCOUNT.csv", waste_discount_rows)
print("CSV生成完了！")