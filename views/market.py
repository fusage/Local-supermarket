# -*- coding: utf-8 -*-
"""競合・地域情報(ウェブから集めた情報を表示する画面)

  ① 競合チラシ情報 … タブで店舗を切り替える
       スギ薬局 金沢駅西店 : lib/crawler/competitor_flyer_scraper.py
                             「更新」ボタンを押したときだけ取得し、DBに保存する。
                             画面にはDBに保存してある最新の取得分を表示する。
       イオン金沢店        : 情報取得準備中
  ② 来月のイベント … lib/crawler/kanazawa_events_scraper.py
                       画面を開いたときに取得する(1時間キャッシュ)。失敗したら前回の保存分を表示する。
  ③ 金沢市の天気   … 現在準備中
"""
from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from lib import charts as ch
from lib import crawl_store

logger = logging.getLogger(__name__)

try:  # クローラー用のライブラリが無い環境でも、ほかの画面は動くようにする
    from lib.crawler.competitor_flyer_scraper import TokubaiFlyerScraper
    from lib.crawler.kanazawa_events_scraper import fetch_next_month_events
    IMPORT_ERROR = None
except Exception as e:  # noqa: BLE001
    IMPORT_ERROR = str(e)

SUGI = "スギ薬局 金沢駅西店"
SUGI_PAGE_URL = "https://www.sugi-net.jp/stores/001612"
AEON = "イオン金沢店"
EVENT_CACHE_SEC = 60 * 60  # イベントの取得結果を1時間使い回す(相手サイトへのアクセスを減らすため)


# ---------------------------------------------------------------------------
# ① 競合チラシ情報
# ---------------------------------------------------------------------------
def update_sugi() -> None:
    """スギ薬局のチラシと特売商品を取得し、DBの保存分を置き換える(「更新」ボタンから呼ぶ)。"""
    scraper = TokubaiFlyerScraper.from_store_page(SUGI_PAGE_URL)
    flyers = scraper.fetch_flyers(count=6)
    items = []
    if flyers:
        try:
            items = scraper.fetch_featured_items(flyers[0]["detail_url"])
        except Exception as e:  # noqa: BLE001  特売商品は補助情報なので、失敗してもチラシは保存する
            logger.warning("特売商品の取得に失敗: %s", e)
    crawl_store.save("crawl_flyers", flyers, SUGI)
    if items:
        crawl_store.save("crawl_featured_items", items, SUGI)


def _sugi_tab() -> None:
    if st.button("🔄 最新のチラシ情報を取得", help="スギ薬局・トクバイのサイトから取得します(30秒ほどかかります)"):
        with st.spinner("チラシ情報を取得中...(30秒ほどかかります)"):
            try:
                update_sugi()
                st.success("最新のチラシ情報を取得しました。")
            except Exception as e:  # noqa: BLE001
                logger.exception("チラシの取得に失敗")
                st.error(f"チラシ情報の取得に失敗しました。前回取得した分を表示します。\n\n詳細: {e}")

    # 表示は常にDBの保存分から
    flyers, fetched_at = crawl_store.load("crawl_flyers", SUGI)
    items, _ = crawl_store.load("crawl_featured_items", SUGI)
    if fetched_at is None:
        st.info("まだチラシ情報を取得していません。「最新のチラシ情報を取得」を押してください。")
        return
    st.caption(f"前回取得：{fetched_at}")

    if not flyers:
        st.info("前回取得した時点では、掲載中のチラシはありませんでした。")
        return

    # --- チラシ一覧と画像 ---
    st.dataframe(
        pd.DataFrame(flyers)[["title", "period_start", "period_end", "detail_url"]],
        hide_index=True,
        width="stretch",
        column_config={
            "title": "チラシ名",
            "period_start": "掲載開始",
            "period_end": "掲載終了",
            "detail_url": st.column_config.LinkColumn("チラシページ"),
        },
    )
    cols = st.columns(3)
    for i, f in enumerate(flyers):
        with cols[i % 3]:
            if f["image_url"]:
                st.image(f["image_url"], caption=f"{f['title']}({f['period_start']}〜{f['period_end']})",
                         width="stretch")

    # --- 特売商品(店舗が掲載している「今週のおすすめ/イチオシ」) ---
    if not items:
        return
    st.subheader("特売商品")
    st.caption("トクバイの店舗ページ(取得日・翌日・翌々日)に載っている「今週のおすすめ/イチオシ」です。"
               "チラシの全商品ではなく、店舗が選んだ商品のみです。")
    df = pd.DataFrame(items)
    df["is_featured"] = df["is_featured"].fillna(0).astype(bool)
    dates = sorted(df["sale_date"].unique())
    picked = st.multiselect("日付で絞り込み", dates, default=dates)
    shown = df[df["sale_date"].isin(picked)]

    st.dataframe(
        shown,
        hide_index=True,
        width="stretch",
        column_order=["sale_date", "product_name", "spec", "base_price", "tax_price",
                      "is_featured", "image_url", "product_url"],
        column_config={
            "sale_date": "日付",
            "product_name": "商品名",
            "spec": "内容",
            "base_price": st.column_config.NumberColumn("本体価格(円)", format="%d"),
            "tax_price": st.column_config.NumberColumn("税込価格(円)", format="%d"),
            "is_featured": "イチオシ",
            "image_url": st.column_config.ImageColumn("画像"),
            "product_url": st.column_config.LinkColumn("商品ページ"),
        },
    )
    st.download_button(
        "特売商品をCSVでダウンロード",
        shown.drop(columns=["image_url"]).to_csv(index=False).encode("utf-8-sig"),
        file_name="sugi_featured_items.csv",
        mime="text/csv",
    )


def _section_flyers() -> None:
    st.header("競合チラシ情報")
    tab_sugi, tab_aeon = st.tabs([SUGI, AEON])  # 左がスギ薬局
    with tab_sugi:
        _sugi_tab()
    with tab_aeon:
        st.info("情報取得準備中")


# ---------------------------------------------------------------------------
# ② 来月のイベント
# ---------------------------------------------------------------------------
@st.cache_data(ttl=EVENT_CACHE_SEC, show_spinner=False)
def load_events() -> tuple[list, str | None]:
    """来月のイベントを取得してDBに保存する。戻り値: (イベント一覧, エラー文)。失敗しても例外は投げない。"""
    try:
        events = fetch_next_month_events()
    except Exception as e:  # noqa: BLE001
        logger.exception("イベントの取得に失敗")
        return [], str(e)
    if events:
        try:
            crawl_store.save("crawl_events", events)
        except Exception as e:  # noqa: BLE001  保存に失敗しても画面の表示は続ける
            logger.warning("イベントの保存に失敗: %s", e)
    return events, None


def _section_events() -> None:
    st.header("来月のイベント(金沢市)")
    col_reload, col_view = st.columns([1, 3])
    with col_reload:
        if st.button("🔄 イベント情報を更新"):
            load_events.clear()
    with col_view:
        view = st.radio("表示形式", ["テーブル", "カード(画像付き)"], horizontal=True,
                        label_visibility="collapsed")

    with st.spinner("金沢市公式サイトからイベント情報を取得中..."):
        events, error = load_events()
    if error:  # 取得に失敗したら、前回DBに保存した分を表示する
        events, fetched_at = crawl_store.load("crawl_events")
        if events:
            st.warning(f"イベント情報を取得できなかったため、前回保存した分({fetched_at} 取得)を表示しています。"
                       f"\n\n詳細: {error}")
        else:
            st.error(f"イベント情報の取得に失敗しました。\n\n詳細: {error}")

    if not events:
        if not error:
            st.warning("来月開催のイベントが0件でした。ページの構造が変わっている可能性があります。")
        return

    if view == "テーブル":
        st.dataframe(
            pd.DataFrame(events),
            hide_index=True,
            width="stretch",
            column_order=["title", "period_raw", "venue", "category", "description", "url"],
            column_config={
                "title": "イベント名",
                "period_raw": "日時",
                "venue": "場所・会場",
                "category": "分野",
                "description": "説明",
                "url": st.column_config.LinkColumn("詳細ページ"),
            },
        )
        return

    cols = st.columns(3)
    for i, e in enumerate(events):
        with cols[i % 3], st.container(border=True):
            if e["image_url"]:
                st.image(e["image_url"], width="stretch")
            st.markdown(f"**{e['title']}**")
            if e["period_raw"]:
                st.caption(f"🗓️ {e['period_raw']}")
            if e["venue"]:
                st.caption(f"📍 {e['venue']}")
            if e["description"]:
                st.write(e["description"])
            st.markdown(f"[詳細を見る]({e['url']})")


# ---------------------------------------------------------------------------
# ③ 金沢市の天気
# ---------------------------------------------------------------------------
def _section_weather() -> None:
    st.header("金沢市の天気")
    st.info("現在準備中")


# ---------------------------------------------------------------------------
def render() -> None:
    st.subheader("競合・地域情報")
    st.caption("競合店のチラシ・特売と、地域のイベントをウェブから集めて表示する画面です。")
    st.sidebar.info("この画面は外部サイトから実際の情報を取得します(ダミーデータではありません)。"
                    "取得結果は統合DBの crawl_* テーブルに保存されます。", icon="ℹ️")

    if IMPORT_ERROR:
        st.error("クローラーに必要なライブラリを読み込めませんでした。"
                 f"`pip install -r requirements.txt` を実行してください。\n\n詳細: {IMPORT_ERROR}")
    else:
        _section_flyers()
        _section_events()
    _section_weather()
