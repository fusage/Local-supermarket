# -*- coding: utf-8 -*-
"""ロス分析（データ班のテーブルと分析ビューを表示する画面）

データは data/csv/*.csv（M_* / T_*）→ 統合DB、集計は scripts/analysis_views.sql のビュー3本。
  ① 店舗別 3大ロス        … V_STORE_LOSS_SUMMARY
  ② 気温急変日の発注検証  … V_WEATHER_HYPOTHESIS_CHECK
  ③ 夕方の見切り候補      … V_EVENING_DISCOUNT_CANDIDATES
"""
from __future__ import annotations

import streamlit as st

from lib import charts as ch
from lib import db

LOSS_COLS = [
    ("total_waste_loss", "廃棄ロス"),
    ("total_discount_loss", "値引ロス"),
    ("total_opportunity_loss", "機会ロス"),
]


def _yen(v: float) -> str:
    """1週間ぶんの小さな金額なので、万円に丸めず円のまま表示する。"""
    return f"{v:,.0f}円"


def _man(v: float) -> str:
    """上段の指標用（桁が多いと欄からはみ出すため、万円・小数1桁にする）。"""
    return f"{v / 1e4:,.0f}万円" if abs(v) >= 1e6 else f"{v / 1e4:,.1f}万円"


def _tab_summary() -> None:
    df = db.get_loss_summary()
    if df.empty:
        st.info("ロスの集計データがありません。")
        return

    sales = df["total_sales_amount"].sum()
    total = df["total_loss_amount"].sum()
    c = st.columns(6)
    c[0].metric("売上", _man(sales))
    c[1].metric("3大ロス合計", _man(total))
    c[2].metric("ロス率（売上比）", ch.pct(total / sales * 100 if sales else None))
    for i, (col, name) in enumerate(LOSS_COLS):
        c[i + 3].metric(name, _man(df[col].sum()))

    left, right = st.columns([3, 2])
    with left:
        st.markdown("##### 店舗別の3大ロス")
        st.plotly_chart(ch.grouped_bar(df, "store_name", LOSS_COLS, unit="円"), width="stretch")
        st.caption("廃棄ロス＝廃棄数×原価、値引ロス＝見切りの値引額、機会ロス＝欠品で売り逃した推定売上。")
    with right:
        st.markdown("##### ロス合計の多い店舗")
        rank = df.copy()
        rank["loss_rate"] = rank["total_loss_amount"] / rank["total_sales_amount"] * 100
        st.plotly_chart(
            ch.ranking_bar(rank, "store_name", "total_loss_amount",
                           text=[f"{_yen(v)}（{r:.1f}%）" for v, r in
                                 rank.sort_values("total_loss_amount")[
                                     ["total_loss_amount", "loss_rate"]].itertuples(index=False)],
                           color=ch.STATUS["serious"], pad=0.9),
            width="stretch")
        st.caption("カッコ内は売上に対するロスの割合です。")

    st.dataframe(
        df[["store_name", "trade_area_type", "total_sales_amount", "estimated_gross_profit",
            "total_waste_loss", "total_discount_loss", "total_opportunity_loss", "total_loss_amount"]],
        hide_index=True, width="stretch",
        column_config={
            "store_name": "店舗",
            "trade_area_type": "商圏タイプ",
            "total_sales_amount": st.column_config.NumberColumn("売上", format="localized"),
            "estimated_gross_profit": st.column_config.NumberColumn("推定粗利", format="localized"),
            "total_waste_loss": st.column_config.NumberColumn("廃棄ロス", format="localized"),
            "total_discount_loss": st.column_config.NumberColumn("値引ロス", format="localized"),
            "total_opportunity_loss": st.column_config.NumberColumn("機会ロス", format="localized"),
            "total_loss_amount": st.column_config.NumberColumn("ロス合計", format="localized"),
        })


def _tab_weather() -> None:
    df = db.get_weather_hypothesis()
    if df.empty:
        st.info("気温が急に下がった日のデータがありません。")
        return
    st.caption(
        "前日より2.5℃以上気温が下がった日について、AIが推奨した発注数と、"
        "店長が実際に発注した数、その結果（欠品・機会ロス）を並べています。")

    days = (df[["date", "weather", "temp_diff_prev_day"]].drop_duplicates()
              .sort_values("date", ascending=False))
    labels = {r.date: f"{r.date[5:].replace('-', '/')} {r.weather} {r.temp_diff_prev_day:+.1f}℃"
              for r in days.itertuples()}
    picked = st.multiselect("対象日（天気・前日との気温差）", list(labels), default=list(labels),
                            format_func=labels.get, key="loss_weather_days")
    df = df[df["date"].isin(picked)]
    if df.empty:
        st.info("対象日を選んでください。")
        return

    by_store = (df.groupby("store_name", as_index=False)
                  .agg(ai=("ai_recommended_qty", "sum"), final=("final_order_qty", "sum"),
                       lost=("opportunity_loss", "sum"), hours=("out_of_stock_hours", "sum")))
    by_store["gap_rate"] = (by_store["final"] / by_store["ai"] - 1) * 100

    left, right = st.columns(2)
    with left:
        st.markdown("##### AIの推奨に対する発注数の差")
        st.plotly_chart(ch.diverging_bar(by_store, "store_name", "gap_rate"), width="stretch")
        st.caption("マイナス＝AIの推奨より少なく発注した店舗。")
    with right:
        st.markdown("##### 機会ロス（欠品で売り逃した推定売上）")
        st.plotly_chart(
            ch.ranking_bar(by_store, "store_name", "lost",
                           text=[_yen(v) for v in by_store.sort_values("lost")["lost"]],
                           color=ch.STATUS["critical"]),
            width="stretch")
        st.caption("推奨どおりに発注した店舗ほど、機会ロスが小さくなっています。")

    st.markdown("##### 明細（機会ロスの大きい順）")
    st.dataframe(
        df.sort_values("opportunity_loss", ascending=False)[
            ["date", "store_name", "product_name", "category", "ai_recommended_qty",
             "final_order_qty", "order_gap", "actual_sales_qty", "out_of_stock_hours",
             "opportunity_loss", "manager_memo"]],
        hide_index=True, width="stretch", height=380,
        column_config={
            "date": "納品日", "store_name": "店舗", "product_name": "商品名", "category": "部門",
            "ai_recommended_qty": st.column_config.NumberColumn("AI推奨数", format="localized"),
            "final_order_qty": st.column_config.NumberColumn("発注数", format="localized"),
            "order_gap": st.column_config.NumberColumn("差", format="%+d"),
            "actual_sales_qty": st.column_config.NumberColumn("販売数", format="localized"),
            "out_of_stock_hours": st.column_config.NumberColumn("欠品時間", format="%.1f h"),
            "opportunity_loss": st.column_config.NumberColumn("機会ロス", format="localized"),
            "manager_memo": "店長メモ",
        })


def _tab_discount() -> None:
    df = db.get_discount_candidates()
    if df.empty:
        st.info("見切り候補のデータがありません。")
        return
    st.caption(
        "日持ち2日以内の商品について、発注数から販売数を引いた「残りの見込み」をもとに、"
        "夕方の見切り（値引き）の目安を出しています。")

    c1, c2, c3 = st.columns([1, 1, 1])
    dates = sorted(df["date"].unique(), reverse=True)
    day = c1.selectbox("日付", dates, key="loss_discount_day")
    stores = ["全店"] + sorted(df["store_name"].unique())
    store = c2.selectbox("店舗", stores, key="loss_discount_store")
    only = c3.toggle("値引き推奨のみ", value=True, key="loss_discount_only")

    shown = df[df["date"] == day]
    if store != "全店":
        shown = shown[shown["store_name"] == store]
    need = shown[~shown["discount_recommendation"].str.startswith("定価")]

    m = st.columns(3)
    m[0].metric("対象商品", f"{len(shown):,} 件")
    m[1].metric("値引き推奨", f"{len(need):,} 件")
    m[2].metric("残りの見込み（値引き推奨分）",
                f'{int(need["remaining_stock_estimate"].sum()):,} 点')

    if only:
        shown = need
    if shown.empty:
        st.success("この条件では、値引きが必要な商品はありません（定価で売り切れる見込み）。")
        return
    st.dataframe(
        shown.sort_values("remaining_stock_estimate", ascending=False)[
            ["store_name", "product_name", "category", "sales_price", "initial_order_qty",
             "sales_qty_so_far", "remaining_stock_estimate", "discount_recommendation"]],
        hide_index=True, width="stretch",
        column_config={
            "store_name": "店舗", "product_name": "商品名", "category": "部門",
            "sales_price": st.column_config.NumberColumn("売価", format="localized"),
            "initial_order_qty": st.column_config.NumberColumn("発注数", format="localized"),
            "sales_qty_so_far": st.column_config.NumberColumn("販売数", format="localized"),
            "remaining_stock_estimate": st.column_config.NumberColumn("残りの見込み", format="localized"),
            "discount_recommendation": "見切りの目安",
        })


def render() -> None:
    st.subheader("ロス分析")
    if not db.table_exists("V_STORE_LOSS_SUMMARY"):
        st.info("データ班のテーブルがまだ統合されていません。"
                "`python scripts/build_db.py` を実行してください。")
        return

    start, end = db.loss_period()
    n_store = len(db.get_loss_summary())
    st.caption(
        f"廃棄・値引・機会ロスの「3大ロス」と、気温の急変に対する発注の検証です。"
        f"　対象：{start} 〜 {end}・{n_store}店舗（惣菜・日配・生鮮の代表商品）")
    if db.db_meta().get("real_from"):
        st.sidebar.info(
            "この画面はデータ班のテーブル（M_* / T_*）をそのまま使っています。"
            "統合ダッシュボードと同じ店舗・商品で、この期間の売上・廃棄・欠品の数字は一致します。",
            icon="ℹ️")
    else:
        st.sidebar.info(
            "この画面はデータ班のテーブル（M_* / T_*）を使っています。"
            "統合ダッシュボードとは店舗・商品・期間が別のデータです。", icon="ℹ️")

    tab1, tab2, tab3 = st.tabs(["店舗別 3大ロス", "気温急変日の発注検証", "夕方の見切り候補"])
    with tab1:
        _tab_summary()
    with tab2:
        _tab_weather()
    with tab3:
        _tab_discount()
