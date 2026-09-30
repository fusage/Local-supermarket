import csv
import os
import sys

# 1つ上の階層にある lib/paths.py からCSVの置き場所を読み込む設定
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.paths import CSV_DIR

# 1. 店舗マスタデータ定義（商圏タイプ付き）
stores = [
    ["store_cd", "store_name", "trade_area_type", "address"],
    ["ST001", "サンフジ中央店", "駅前・オフィス", "富山市桜町1丁目"],
    ["ST002", "サンフジ南店", "住宅地", "富山市堀川町"],
    ["ST003", "サンフジ北店", "住宅地（高齢化）", "富山市水橋"],
    ["ST004", "サンフジバイパス店", "ロードサイド", "富山市婦中町"],
    ["ST005", "サンフジ港店", "工業地帯・港湾", "富山市東岩瀬町"],
]

# 2. 商品マスタデータ定義（惣菜・日配・生鮮にフォーカス）
products = [
    [
        "product_cd",
        "product_name",
        "category",
        "cost_price",
        "sales_price",
        "shelf_life_days",
    ],
    ["SOZ001", "特製ロースカツ弁当", "惣菜", 240, 480, 1],
    ["SOZ002", "手作り鶏唐揚げ(大)", "惣菜", 180, 360, 1],
    ["SOZ003", "国産豚の生姜焼き弁当", "惣菜", 220, 450, 1],
    ["SOZ004", "彩りポテトサラダ", "惣菜", 90, 180, 2],
    ["NIP001", "本格寄せ鍋つゆ(醤油)", "日配", 130, 260, 30],
    ["NIP002", "濃厚白湯鍋つゆ", "日配", 130, 260, 30],
    ["NIP003", "もっちり木綿豆腐", "日配", 40, 80, 3],
    ["NIP004", "絹ごし冷奴用豆腐", "日配", 35, 70, 3],
    ["NIP005", "讃岐うどん(3食入)", "日配", 70, 140, 5],
    ["SEI001", "国産豚バラ肉(鍋用スライス)", "生鮮", 180, 320, 2],
    ["SEI002", "若鶏もも肉(業務用カット)", "生鮮", 120, 220, 2],
    ["SEI003", "鍋用カット野菜セット", "生鮮", 90, 180, 2],
]

# 3. 気象データ定義（気温差感応度ロジック用）
weathers = [
    [
        "date",
        "region_cd",
        "weather",
        "max_temp",
        "min_temp",
        "temp_diff_prev_day",
    ],
    ["2026-09-20", "REG01", "晴れ", 26.5, 18.0, 0.5],
    ["2026-09-21", "REG01", "曇り", 24.0, 16.5, -2.5],
    ["2026-09-22", "REG01", "雨", 20.0, 13.0, -4.0],
    ["2026-09-23", "REG01", "曇り", 19.5, 12.0, -0.5],
    ["2026-09-24", "REG01", "晴れ", 22.0, 14.0, 2.5],
    ["2026-09-25", "REG01", "晴れ", 23.5, 15.0, 1.5],
    ["2026-09-26", "REG01", "雨", 18.0, 11.5, -5.5],
]


def save_to_csv(filename, data):
    filepath = os.path.join(CSV_DIR, filename)
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerows(data)


save_to_csv("M_STORE.csv", stores)
save_to_csv("M_PRODUCT.csv", products)
save_to_csv("M_WEATHER.csv", weathers)

print("=== CSVマスタ生成完了 ===")
print(f"出力先: {CSV_DIR}")