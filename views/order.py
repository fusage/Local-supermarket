# -*- coding: utf-8 -*-
"""明日の発注提案（店舗チーフが朝に使う画面）

明日の天気予報と、過去の急変日の売れ方から、明日の発注数のおすすめと理由を出します。
計算は lib/forecast.py。データはデータ班のテーブル（M_* / T_*）を lib/db.py の loss_q で読みます
（GCPの設定があれば BigQuery、無ければ SQLite）。

データ班のデータは 2026-09-20〜26 の1週間なので、発注日はその期間から選びます（デモでは 9/25・南店）。
「答え合わせ」を押すと、翌日に実際に起きたこと（店長の発注・欠品・売り逃し）が見られます。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import db
from lib import forecast as fc

DEFAULT_STORE = "ST002"          # 南店（ストーリーの主人公の店）


@st.cache_data(show_spinner=False)
def _tables() -> dict[str, pd.DataFrame]:
    return {t: db.loss_q(f"SELECT * FROM {t}") for t in fc.TABLES}


def _md(s: str) -> str:
    return f"{int(s[5:7])}/{int(s[8:10])}"


def _weather_card(wx: dict) -> None:
    if not wx:
        st.info("明日の天気のデータがありません。")
        return
    c1, c2 = st.columns([1, 2])
    c1.metric(f"明日 {_md(wx['date'])} {wx['weather']}・最高気温", f"{wx['max_temp']:.1f}℃",
              f"{wx['temp_diff']:+.1f}℃（前日比）", delta_color="inverse")
    with c2:
        if wx["cold"]:
            st.warning("**気温急変日です。** 前日より大きく冷え込むため、天気に反応する商品の需要が増える見込みです。",
                       icon="🌡️")
        else:
            st.success("急変日ではありません。普段の需要どおりで大丈夫な見込みです。", icon="🌤️")


def _answer(d: fc.Data, store_cd: str, store_name: str, order_date: str, rec: pd.DataFrame) -> None:
    ans = fc.answer(d, store_cd, order_date, rec)
    delivery = (pd.Timestamp(order_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    memos = ans["manager_memo"].dropna().unique()
    st.markdown(f"##### 答え合わせ：{_md(delivery)}に{store_name}で実際に起きたこと")
    c1, c2, c3 = st.columns(3)
    c1.metric("欠品時間（合計）", f"{ans['out_of_stock_hours'].sum():.1f}時間",
              help="全商品の欠品時間の合計")
    c2.metric("売り逃し", f"{ans['lost_sales_est_amount'].sum():,.0f}円")
    c3.metric("防げた売り逃し", f"{ans['saved'].sum():,.0f}円",
              help="おすすめ数どおりに発注していたら取り戻せた売り逃し（追加で発注した数まで）")
    if len(memos):
        st.caption("この日の店長の発注メモ：" + "／".join(f"「{m}」" for m in memos))
    st.dataframe(
        ans[["product_name", "recommend", "final_order_qty", "demand", "qty", "out_of_stock_hours",
             "lost_sales_est_amount", "saved", "manager_memo"]],
        hide_index=True, width="stretch",
        column_config={
            "product_name": "商品名",
            "recommend": st.column_config.NumberColumn("おすすめ数", format="%d"),
            "final_order_qty": st.column_config.NumberColumn("実際の発注", format="%d"),
            "demand": st.column_config.NumberColumn("需要", format="%d", help="売れた数＋売り逃した数"),
            "qty": st.column_config.NumberColumn("売れた数", format="%d"),
            "out_of_stock_hours": st.column_config.NumberColumn("欠品時間", format="%.1f時間"),
            "lost_sales_est_amount": st.column_config.NumberColumn("売り逃し", format="%d円"),
            "saved": st.column_config.NumberColumn("防げた売り逃し", format="%d円"),
            "manager_memo": "発注メモ",
        })


def render() -> None:
    st.subheader("明日の発注提案")
    st.caption("明日の天気予報と、過去の急変日の売れ方から、明日の発注数のおすすめと理由を出します。"
               "（データ班の1週間ぶんのデータで、その日の朝を再現しています）")

    try:
        d = fc.Data.from_tables(_tables())
    except Exception as e:  # noqa: BLE001
        st.error(f"データ班のテーブルを読み込めませんでした。`python scripts/build_db.py` を実行してください。\n\n詳細: {e}")
        return

    stores = d.stores.sort_values("store_cd")
    names = dict(zip(stores["store_cd"], stores["store_name"]))
    dates = fc.order_dates(d)
    c1, c2 = st.columns(2)
    store_cd = c1.selectbox("店舗", list(names), index=list(names).index(DEFAULT_STORE) if DEFAULT_STORE in names else 0,
                            format_func=lambda c: names[c], key="order_store")
    order_date = c2.selectbox("発注する日（その日の朝）", dates, index=len(dates) - 1,
                              format_func=lambda s: f"{_md(s)}（{_md((pd.Timestamp(s) + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))}納品ぶん）",
                              key="order_date")

    rec, wx = fc.recommend(d, store_cd, order_date)
    _weather_card(wx)

    reacts = rec[rec["reacts"]]
    if wx.get("cold_dates") and not reacts.empty:
        st.markdown(
            f"過去の急変日（{'・'.join(_md(x) for x in wx['cold_dates'])}）の売れ方から、"
            f"**天気に反応する商品を {len(reacts)} 品**見つけました："
            + "、".join(reacts["product_name"]))
    elif wx.get("cold") and not wx.get("cold_dates"):
        st.info("過去に急変日の実績がまだ無いため、天気の影響を見込めていません（実績が貯まると見込めるようになります）。")

    st.markdown("##### 明日の発注数のおすすめ")
    st.dataframe(
        rec[["product_name", "category", "usual", "factor", "recommend", "diff"]],
        hide_index=True, width="stretch",
        column_config={
            "product_name": "商品名", "category": "部門",
            "usual": st.column_config.NumberColumn("普段どおりなら", format="%d個",
                                                   help="急変でない日の需要（売れた数＋売り逃した数）の平均"),
            "factor": st.column_config.NumberColumn("急変日の倍率", format="%.2f倍",
                                                    help="過去の急変日に、普段の何倍売れたか（全店）"),
            "recommend": st.column_config.NumberColumn("おすすめ数", format="%d個"),
            "diff": st.column_config.NumberColumn("普段との差", format="%+d個"),
        })

    pick = st.selectbox("理由を見る商品", rec["product_name"], key="order_pick")
    st.info(rec.loc[rec["product_name"] == pick, "reason"].iloc[0], icon="💡")

    key = f"answer_{store_cd}_{order_date}"
    if st.button("🔍 答え合わせ（翌日の結果を見る）", key=f"btn_{key}"):
        st.session_state[key] = True
    if st.session_state.get(key):
        _answer(d, store_cd, names[store_cd], order_date, rec)

    st.caption(
        "計算のしかた：需要＝売れた数＋売り逃した数（欠品の日は販売数が少なく出るため）。"
        "急変日＝最高気温が前日より3.0℃以上下がり、最高25℃未満の日。"
        "天気の倍率は全店の実績から商品ごとに出し、1.15倍以上の商品だけ天気に反応するとみなします。"
        "理由の文は数字から組み立てています（生成AIは使っていません）。")
