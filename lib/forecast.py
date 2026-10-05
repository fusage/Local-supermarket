# -*- coding: utf-8 -*-
"""天気を見る発注提案（「明日の発注提案」画面 views/order.py の計算）

データ班のテーブル（M_* / T_*）だけで、明日の発注数のおすすめと、その理由を出します。

  需要         ＝ 販売数 ＋ 売り逃した数（T_INVENTORY の売り逃し額 ÷ 売価）
                  欠品した日は販売数が本当の需要より少なく出るので、売り逃しぶんを足して数える
  普段の需要   ＝ 発注日より前の「急変でない日」の、その店・その商品の需要の平均
  天気の倍率   ＝ 発注日より前の急変日の需要 ÷ 普段の需要（全店をまとめて、商品ごとに計算）
                  倍率が REACT 以上の商品だけ「天気に反応する商品」として扱う（鍋関連を決め打ちしない）
  おすすめ数   ＝ 明日が急変日で、天気に反応する商品なら 普段の需要 × 倍率、それ以外は普段の需要

急変日の条件は、データの作り方（lib/real_data.py・scripts/create_transactions.py）と同じ
「最高気温の前日差が −3.0℃以下、かつ最高気温25℃未満」。
発注日の当日の販売はまだ終わっていないので、使うのは発注日の前日までの実績です。
在庫の差し引きはしていません（データ班のデータでは前日の残りが翌日の在庫に足されないため）。

このファイルは streamlit を読み込みません。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from lib.real_data import COLD_SNAP, NABE_MAX_TEMP

REACT = 1.15          # 急変日に普段のこれ倍以上売れた商品を「天気に反応する商品」とみなす

TABLES = ("M_STORE", "M_PRODUCT", "M_WEATHER", "T_SALES", "T_ORDER", "T_INVENTORY", "T_WASTE_DISCOUNT")


@dataclass
class Data:
    """データ班のテーブル一式（views/order.py が db.loss_q で読んで渡す）。"""
    stores: pd.DataFrame
    products: pd.DataFrame
    weather: pd.DataFrame
    sales: pd.DataFrame
    orders: pd.DataFrame
    inventory: pd.DataFrame
    waste: pd.DataFrame

    @classmethod
    def from_tables(cls, t: dict[str, pd.DataFrame]) -> "Data":
        return cls(t["M_STORE"], t["M_PRODUCT"], t["M_WEATHER"], t["T_SALES"],
                   t["T_ORDER"], t["T_INVENTORY"], t["T_WASTE_DISCOUNT"])


def is_cold_snap(temp_diff: float, max_temp: float) -> bool:
    return pd.notna(temp_diff) and temp_diff <= COLD_SNAP and max_temp < NABE_MAX_TEMP


def demand(d: Data) -> pd.DataFrame:
    """日・店・商品ごとの需要（販売数＋売り逃し数）と、その日が急変日かどうか。"""
    s = d.sales[["sales_date", "store_cd", "product_cd", "qty"]].rename(columns={"sales_date": "date"})
    inv = d.inventory[["date", "store_cd", "product_cd", "lost_sales_est_amount", "out_of_stock_hours"]]
    df = (s.merge(inv, on=["date", "store_cd", "product_cd"], how="left")
           .merge(d.products[["product_cd", "sales_price"]], on="product_cd"))
    df["lost_qty"] = (df["lost_sales_est_amount"].fillna(0) / df["sales_price"]).round()
    df["demand"] = df["qty"] + df["lost_qty"]
    w = d.weather.drop_duplicates("date").set_index("date")
    df["cold"] = [is_cold_snap(w["temp_diff_prev_day"].get(x), w["max_temp"].get(x)) for x in df["date"]]
    return df


def order_dates(d: Data) -> list[str]:
    """発注できる日（＝翌日の結果が分かっている日）。"""
    return sorted(d.orders["order_date"].astype(str).unique())


def tomorrow_weather(d: Data, delivery_date: str) -> dict:
    w = d.weather[d.weather["date"] == delivery_date]
    if w.empty:
        return {}
    r = w.iloc[0]
    return {"date": delivery_date, "weather": r["weather"], "max_temp": float(r["max_temp"]),
            "min_temp": float(r["min_temp"]), "temp_diff": float(r["temp_diff_prev_day"]),
            "cold": is_cold_snap(r["temp_diff_prev_day"], r["max_temp"])}


def recommend(d: Data, store_cd: str, order_date: str) -> tuple[pd.DataFrame, dict]:
    """発注日の朝に出す、翌日（納品日）ぶんのおすすめ。戻り値は（商品別の表, 明日の天気）。"""
    delivery = (pd.Timestamp(order_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    wx = tomorrow_weather(d, delivery)
    dem = demand(d)
    past = dem[dem["date"] < order_date]            # 発注日の前日までに分かっている実績

    normal = (past[(~past["cold"]) & (past["store_cd"] == store_cd)]
              .groupby("product_cd")["demand"].mean().rename("base"))
    # 天気の倍率：全店まとめて「急変日の需要 ÷ 同じ店の普段の需要」を平均する
    store_normal = past[~past["cold"]].groupby(["store_cd", "product_cd"])["demand"].mean().rename("normal")
    cold = past[past["cold"]].merge(store_normal, on=["store_cd", "product_cd"])
    cold["ratio"] = cold["demand"] / cold["normal"]
    factor = cold.groupby("product_cd")["ratio"].mean().rename("factor")
    cold_dates = sorted(past.loc[past["cold"], "date"].unique())

    out = d.products[["product_cd", "product_name", "category", "sales_price"]].merge(
        normal, on="product_cd", how="left").merge(factor, on="product_cd", how="left")
    # 普段の需要の履歴が無ければ、前回の発注数
    last = (d.orders[(d.orders["store_cd"] == store_cd) & (d.orders["order_date"] < order_date)]
            .sort_values("order_date").groupby("product_cd")["final_order_qty"].last())
    out["base"] = out["base"].fillna(out["product_cd"].map(last))
    out["factor"] = out["factor"].fillna(1.0)
    out["reacts"] = out["factor"] >= REACT
    use = bool(wx.get("cold")) & out["reacts"]
    out["usual"] = out["base"].round()
    out["recommend"] = (out["base"] * out["factor"].where(use, 1.0)).round()
    out["diff"] = out["recommend"] - out["usual"]
    out["reason"] = [_reason(r, wx, cold_dates) for r in out.itertuples()]
    out = out.sort_values(["diff", "product_cd"], ascending=[False, True]).reset_index(drop=True)
    return out, {**wx, "cold_dates": cold_dates, "order_date": order_date}


def _md(s: str) -> str:
    return f"{int(s[5:7])}/{int(s[8:10])}"


def _reason(r, wx: dict, cold_dates: list[str]) -> str:
    """理由の文（数字から組み立てる。生成AIは使わない）。"""
    if not wx:
        return "明日の天気が分からないため、普段の需要どおりにしています。"
    head = (f"明日（{_md(wx['date'])}）は{wx['weather']}・最高{wx['max_temp']:.1f}℃で、"
            f"前日より{abs(wx['temp_diff']):.1f}℃{'下がる' if wx['temp_diff'] < 0 else '上がる'}予報です。")
    base = f"この店の普段の需要（1日{r.base:.0f}個。欠品で売れなかった分を含む）"
    if not wx["cold"]:
        return head + f"急変日ではないので、{base}どおり{r.recommend:.0f}個をおすすめします。"
    if not cold_dates:
        return (head + "急変日ですが、まだ過去の急変日の実績が無く、天気の影響を見込めていません。"
                f"{base}どおり{r.recommend:.0f}個です。")
    past = "・".join(_md(x) for x in cold_dates)
    if not r.reacts:
        return (head + f"過去の急変日（{past}）も、この商品の売れ方は普段の{r.factor:.2f}倍で、"
                f"天気の影響はほぼありません。{base}どおり{r.recommend:.0f}個をおすすめします。")
    return (head + f"過去の急変日（{past}）には、この商品は全店で普段の{r.factor:.2f}倍売れました。"
            f"{base}に掛けて、{r.recommend:.0f}個をおすすめします（普段どおりより{r.diff:+.0f}個）。")


def answer(d: Data, store_cd: str, order_date: str, rec: pd.DataFrame) -> pd.DataFrame:
    """答え合わせ：翌日（納品日）に実際に起きたことと、おすすめどおりなら防げた売り逃し。"""
    delivery = (pd.Timestamp(order_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    o = d.orders[(d.orders["store_cd"] == store_cd) & (d.orders["delivery_date"] == delivery)]
    dem = demand(d)
    dem = dem[(dem["store_cd"] == store_cd) & (dem["date"] == delivery)]
    w = d.waste[(d.waste["store_cd"] == store_cd) & (d.waste["date"] == delivery)]
    out = (rec[["product_cd", "product_name", "sales_price", "recommend"]]
           .merge(o[["product_cd", "final_order_qty", "manager_memo"]], on="product_cd", how="left")
           .merge(dem[["product_cd", "qty", "demand", "lost_qty", "out_of_stock_hours",
                       "lost_sales_est_amount"]], on="product_cd", how="left")
           .merge(w[["product_cd", "waste_qty"]], on="product_cd", how="left"))
    # おすすめどおり発注していたら、売り逃しのうち取り戻せた数（追加で発注した数まで）
    extra = (out["recommend"] - out["final_order_qty"]).clip(lower=0)
    out["saved"] = (out["lost_qty"].clip(upper=extra) * out["sales_price"]).fillna(0)
    return out
