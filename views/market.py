# -*- coding: utf-8 -*-
"""競合・地域情報（クローラーの取得結果を表示する画面）

  ① 競合チラシ情報 … lib/crawler/competitor_flyer_scraper.py（＋OCR: flyer_item_extractor.py）
  ② 来月のイベント … lib/crawler/kanazawa_events_scraper.py
  ③ 卸売相場       … 農林水産省 青果物卸売市場調査（日別）金沢市中央卸売市場（lib/collect.py）
  ④ 天気           … Open-Meteo（lib/collect.py）

ふだんは、毎朝の自動収集（scripts/collect.py → data/raw → 統合DB）で貯めたぶんの最新を表示します。
まだ1度も集めていないときや「今すぐ取得し直す」を押したときだけ、その場でサイトから取得し、
統合DB（crawl_* テーブル）に保存します。取得に失敗したときは、前回保存したぶんを表示します。
"""
from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from lib import charts as ch
from lib import crawl_store

logger = logging.getLogger(__name__)

try:        # クローラーに必要なライブラリが無い環境でも、ほかの画面は動くようにする
    from lib import collect
    from lib.crawler.flyer_item_extractor import OcrNotAvailableError, extract_items
    IMPORT_ERROR = None
except Exception as e:  # noqa: BLE001
    IMPORT_ERROR = str(e)


def _save(func, *args) -> None:
    """DBへの保存に失敗しても、画面の表示は続ける。"""
    try:
        func(*args)
    except Exception as e:  # noqa: BLE001
        logger.warning("取得結果のDB保存に失敗: %s", e)


# ---------------------------------------------------------------------------
# データ取得(キャッシュ付き)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60 * 60, show_spinner=False)  # 1時間キャッシュ
def load_next_month_events():
    """
    金沢市イベント一覧をその場で取得して全件を保存し、来月開催分に絞り込んだレコードを返す。
    サイト構造の変化等で失敗した場合は例外を送出せず、
    (レコード一覧, エラーメッセージ) のタプルで返す。
    """
    try:
        records = collect.fetch_events()
        if records:
            _save(crawl_store.save_events, records)
        return collect.next_month(records), None
    except Exception as e:
        logger.exception("イベント取得に失敗しました")
        return [], str(e)


@st.cache_data(ttl=60 * 60, show_spinner=False)  # 1時間キャッシュ
def load_flyers():
    """
    競合店のチラシ情報をその場で取得する。店舗ごとに独立して try/except し、
    片方が失敗してももう片方は表示できるようにする。
    戻り値: {店舗名: {"flyers": [dictのリスト], "pickup": [dictのリスト], "error": str | None}}
    """
    results = {}
    for name, fetch in collect.COMPETITORS.items():
        try:
            flyers, pickup = fetch()
            _save(crawl_store.save_flyers, name, flyers)
            if pickup:
                _save(crawl_store.save_featured_items, name, pickup)
            results[name] = {"flyers": flyers, "pickup": pickup, "error": None}
        except Exception as e:
            logger.exception("%s のチラシ取得に失敗", name)
            results[name] = {"flyers": [], "pickup": [], "error": str(e)}
    return results


def saved_flyers() -> dict:
    """自動収集などで保存済みのチラシ（店舗ごとに最新の1回ぶん）。load_flyers と同じ形で返す。"""
    return {name: {"flyers": crawl_store.load_flyers(name),
                   "pickup": crawl_store.featured_as_crawler_records(name), "error": None}
            for name in collect.COMPETITORS}


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)  # 同じ画像は24時間キャッシュ(OCRは1枚数秒かかるため)
def run_flyer_ocr(image_url: str, period_label: str):
    """
    チラシ画像1枚をOCRし、(商品明細, OCRが読んだ生の行, エラー文, 画像メタ情報) を返す。
    Tesseract未導入などの場合は、例外ではなくエラー文で返す。
    """
    try:
        items, raw_lines, meta = extract_items(image_url, period_text=period_label, return_meta=True)
        return items, raw_lines, None, meta
    except OcrNotAvailableError as e:
        return [], [], str(e), None
    except Exception as e:  # noqa: BLE001
        logger.exception("OCRに失敗しました")
        return [], [], f"OCR処理中にエラーが発生しました: {e}", None


# ---------------------------------------------------------------------------
# ①競合チラシ情報
# ---------------------------------------------------------------------------
def _flyer_store_tab(store_name: str, result: dict) -> None:
    if result["error"]:
        # 取得に失敗したら、前回DBに保存したぶんを表示する
        saved = crawl_store.load_flyers(store_name)
        if not saved:
            st.error(f"{store_name} のチラシ取得に失敗しました。\n\n詳細: {result['error']}")
            return
        st.warning(
            f"{store_name} のチラシを取得できなかったため、前回保存したぶん"
            f"（{crawl_store.last_fetched('crawl_flyers', store_name)} 取得）を表示しています。"
            f"\n\n詳細: {result['error']}")
        result = {"flyers": saved, "pickup": crawl_store.featured_as_crawler_records(store_name),
                  "error": None}

    flyers = result["flyers"]
    if not flyers:
        st.info(
            f"{store_name} は現在、チラシの掲載が確認できませんでした。"
            "(店舗ページに掲載がない、または外部チラシサービスのJS描画で取得できていない可能性があります)"
        )
        return

    flyer_df = pd.DataFrame(flyers)
    st.dataframe(
        flyer_df[["title", "period_start", "period_end", "detail_url"]].rename(
            columns={
                "title": "チラシ名",
                "period_start": "掲載開始",
                "period_end": "掲載終了",
                "detail_url": "チラシページ",
            }
        ),
        width="stretch",
        hide_index=True,
        column_config={"チラシページ": st.column_config.LinkColumn("チラシページ")},
    )

    img_cols = st.columns(3)
    for i, f in enumerate(flyers):
        with img_cols[i % 3]:
            if f.get("image_url"):
                st.image(
                    f["image_url"],
                    caption=(
                        f"{f['title']}({f.get('period_start')}〜{f.get('period_end')})"
                        + (f" {f['image_width']}×{f['image_height']}px" if f.get("image_width") else "")
                    ),
                    width="stretch",
                )
            else:
                st.caption(f"{f['title']}:画像を取得できませんでした")

    # ---- トクバイ掲載の特売商品(文字データ) ----
    if result.get("pickup"):
        st.subheader("店舗が掲載している特売商品(文字データ)")
        pickup_df = pd.DataFrame(result["pickup"])
        st.caption(
            f"トクバイの店舗ページ(今日・明日・明後日)に掲載されている「今週のおすすめ/イチオシ」を、"
            f"文字データのまま取得した {len(pickup_df)} 件です(OCRではないため正確です)。"
            "チラシ全商品の一覧ではなく、店舗が選んだ商品のみです。"
        )
        dates = sorted(pickup_df["日付"].unique())
        picked = st.multiselect("日付で絞り込み", dates, default=dates, key=f"pickup_dates_{store_name}")
        shown = pickup_df[pickup_df["日付"].isin(picked)]
        st.dataframe(
            shown[["日付", "商品名", "内容", "本体価格", "税込価格", "イチオシ", "画像URL", "商品URL"]],
            width="stretch",
            hide_index=True,
            column_config={
                "画像URL": st.column_config.ImageColumn("画像"),
                "商品URL": st.column_config.LinkColumn("商品ページ"),
                "本体価格": st.column_config.NumberColumn("本体価格(円)", format="%d"),
                "税込価格": st.column_config.NumberColumn("税込価格(円)", format="%d"),
            },
        )
        st.download_button(
            "特売商品をCSVでダウンロード",
            shown.drop(columns=["画像URL"]).to_csv(index=False).encode("utf-8-sig"),
            file_name="sugi_featured_items.csv",
            mime="text/csv",
            key=f"dl_pickup_{store_name}",
        )

    # ---- 目玉商品リスト(OCR) ----
    st.subheader("目玉商品リスト(OCR)")
    ocr_targets = [f for f in flyers if f.get("image_url")]
    if not ocr_targets:
        st.caption("画像が取得できたチラシがないため、OCRは実行できません。")
        return

    labels = [
        f"{f['title']}({f.get('period_start')}〜{f.get('period_end')}) ID:{f.get('leaflet_id')}"
        for f in ocr_targets
    ]
    chosen = st.selectbox("OCRを実行するチラシ", labels, key=f"sel_{store_name}")
    target = ocr_targets[labels.index(chosen)]
    ocr_key = f"ocr_{store_name}_{target.get('leaflet_id')}"

    if st.button("🔍 この画像から商品明細を抽出(1枚あたり数秒〜十数秒)", key=f"btn_{ocr_key}"):
        st.session_state[ocr_key] = True

    if not st.session_state.get(ocr_key):
        return

    period_label = f"{target.get('period_start')}〜{target.get('period_end')}"
    with st.spinner("OCR実行中..."):
        items, raw_lines, ocr_error, ocr_meta = run_flyer_ocr(target["image_url"], period_label)

    if ocr_meta:
        st.caption(f"OCR対象の画像: {ocr_meta['width']}×{ocr_meta['height']}px  {target['image_url']}")
        if ocr_meta["low_resolution"]:
            st.warning(
                "この画像は解像度が低く(幅800px未満)、OCRの精度が大きく落ちます。"
                "チラシ本体ではなくサムネイル等を取得している可能性があります。"
            )

    if ocr_error:
        st.error(ocr_error)
    elif not items:
        st.warning(
            "商品明細を抽出できませんでした。下の「OCRが読み取った生のテキスト」で、"
            "文字自体が読めているか確認してください。"
        )
    else:
        st.success(f"{len(items)} 件の商品候補を抽出しました。OCRの結果は下書きです。元画像と見比べて修正してください。")
        items_df = pd.DataFrame(items)[
            ["日付", "時間帯", "商品名", "内容", "価格", "価格注記", "別価格", "信頼度"]
        ]
        edited = st.data_editor(
            items_df, width="stretch", hide_index=True, num_rows="dynamic",
            key=f"editor_{ocr_key}",
        )
        st.download_button(
            "CSVでダウンロード",
            edited.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"flyer_items_{target.get('leaflet_id')}.csv",
            mime="text/csv",
            key=f"dl_{ocr_key}",
        )
    if raw_lines:
        with st.expander("OCRが読み取った生のテキスト(突き合わせ確認用)"):
            st.dataframe(pd.DataFrame(raw_lines), width="stretch", hide_index=True)


def _section_flyers() -> None:
    st.header("競合チラシ情報")

    if st.button("🔄 今すぐ取得し直す", key="refetch_flyers"):
        load_flyers.clear()
        st.session_state["flyers_live"] = True

    fetched = crawl_store.last_fetched("crawl_flyers")
    if fetched and not st.session_state.get("flyers_live"):
        flyer_results = saved_flyers()
        st.caption(f"自動収集などで保存したデータ（{fetched} 取得）を表示しています。")
    else:
        with st.spinner("競合店のチラシ情報を取得中...（30秒ほどかかります）"):
            flyer_results = load_flyers()

    store_tabs = st.tabs(list(flyer_results.keys()))
    for tab, (store_name, result) in zip(store_tabs, flyer_results.items()):
        with tab:
            _flyer_store_tab(store_name, result)

    st.caption(
        "※ 目玉商品の明細はチラシ画像をOCR(Tesseract)で読み取った下書きです。"
        "装飾文字・斜め文字などは誤読・欠落するため、元画像と見比べて確認してください。"
    )


# ---------------------------------------------------------------------------
# ②来月のイベント
# ---------------------------------------------------------------------------
def _section_events() -> None:
    st.header("来月のイベント")

    col_reload, col_view = st.columns([1, 3])
    with col_reload:
        if st.button("🔄 今すぐ取得し直す", key="refetch_events"):
            load_next_month_events.clear()  # キャッシュを破棄して再取得させる
            st.session_state["events_live"] = True

    with col_view:
        view_mode = st.radio(
            "表示形式", ["テーブル", "カード(画像付き)"], horizontal=True, label_visibility="collapsed"
        )

    saved, fetched_at = crawl_store.load_events()
    if saved and not st.session_state.get("events_live"):
        records, error = collect.next_month(saved), None
        st.caption(f"自動収集などで保存したデータ（{fetched_at} 取得）を表示しています。")
    else:
        with st.spinner("金沢市公式サイトからイベント情報を取得中..."):
            records, error = load_next_month_events()

    if error:
        # 取得に失敗したら、前回DBに保存したぶんを表示する
        saved, fetched_at = crawl_store.load_events()
        if not saved:
            st.error(
                "イベント情報の取得に失敗しました。サイト構造が変わっている可能性があります。\n\n"
                f"詳細: {error}"
            )
            return
        st.warning(
            f"イベント情報を取得できなかったため、前回保存したぶん（{fetched_at} 取得）を表示しています。"
            f"\n\n詳細: {error}")
        records = collect.next_month(saved)
    elif not records:
        st.warning(
            "来月開催のイベントが0件でした。取得ロジック(セレクタ)が実際のページ構造と "
            "合っていない可能性があるので、実際のページソースを確認してください。"
        )
        return
    else:
        st.success(f"来月開催のイベントを {len(records)} 件取得しました。")

    df = pd.DataFrame(records)

    if view_mode == "テーブル":
        st.dataframe(
            df[["イベント名", "日時", "場所・会場", "分野", "説明", "URL"]],
            width="stretch",
            column_config={
                "URL": st.column_config.LinkColumn("詳細ページ"),
            },
            hide_index=True,
        )
    else:
        # カード形式(画像 + 概要)。3列のグリッドで表示。
        cols = st.columns(3)
        for i, row in enumerate(records):
            with cols[i % 3]:
                with st.container(border=True):
                    if row.get("画像URL"):
                        st.image(row["画像URL"], width="stretch")
                    st.markdown(f"**{row['イベント名']}**")
                    if row.get("日時"):
                        st.caption(f"🗓️ {row['日時']}")
                    if row.get("場所・会場"):
                        st.caption(f"📍 {row['場所・会場']}")
                    if row.get("説明"):
                        st.write(row["説明"])
                    if row.get("URL"):
                        st.markdown(f"[詳細を見る]({row['URL']})")


def render() -> None:
    st.subheader("競合・地域情報")
    st.caption("競合店のチラシ・特売と、地域のイベントをウェブから集めて表示する画面です。")
    st.sidebar.info(
        "この画面は外部サイトから集めた実際の情報です（ダミーデータではありません）。"
        "毎朝自動で集め、統合DBの crawl_* テーブルに履歴として貯めています。", icon="ℹ️")

    if IMPORT_ERROR:
        st.error(
            "クローラーに必要なライブラリを読み込めませんでした。"
            "`pip install -r requirements.txt` を実行してください。\n\n"
            f"詳細: {IMPORT_ERROR}")
        return

    _section_flyers()
    _section_events()
    _section_wholesale()
    _section_weather()


# ---------------------------------------------------------------------------
# ③卸売相場（仕入れ交渉の根拠）
# ---------------------------------------------------------------------------
RECENT_DAYS, BASE_DAYS = 5, 20      # 直近5取引日の平均を、その前の20取引日の平均と比べる


def market_moves(df: pd.DataFrame) -> pd.DataFrame:
    """品目ごとに、直近の相場がその前と比べてどれだけ動いたか（大きく下がった品目が交渉の材料）。"""
    df = df[~df["item"].str.endswith("計") & df["price_per_kg"].notna()]
    dates = sorted(df["date"].unique())
    if len(dates) < RECENT_DAYS + 5:
        return pd.DataFrame()
    recent, base = dates[-RECENT_DAYS:], dates[-(RECENT_DAYS + BASE_DAYS):-RECENT_DAYS]
    now = df[df["date"].isin(recent)].groupby(["category", "item"])["price_per_kg"].mean().rename("now")
    before = df[df["date"].isin(base)].groupby(["category", "item"])["price_per_kg"].mean().rename("before")
    out = pd.concat([now, before], axis=1).dropna().reset_index()
    out["change"] = (out["now"] / out["before"] - 1) * 100
    return out.sort_values("change")


def _section_wholesale() -> None:
    st.header("卸売相場（金沢市中央卸売市場）")
    df = crawl_store.load_market()
    if df.empty:
        st.info("卸売相場はまだ集めていません。毎朝の自動収集（scripts/collect.py）で貯まります。")
        return
    first, last = df["date"].min(), df["date"].max()
    st.caption(f"農林水産省「青果物卸売市場調査（日別調査）」の公表値。{first}〜{last}の"
               f"{df['date'].nunique()}取引日・1kgあたりの卸売価格。公表は約1か月遅れのため、"
               "その日の相場ではなく「相場の傾向」を見る用途です。")

    moves = market_moves(df)
    if not moves.empty:
        st.markdown("##### 相場が下がっている品目")
        st.caption(f"直近{RECENT_DAYS}取引日の平均を、その前の{BASE_DAYS}取引日の平均と比べています。"
                   "相場が下がっているのに仕入れ値が据え置きなら、見直しを求める根拠になります。")
        st.dataframe(
            moves.head(8), hide_index=True, width="stretch",
            column_config={
                "category": "区分", "item": "品目",
                "now": st.column_config.NumberColumn("直近の相場", format="%d円/kg"),
                "before": st.column_config.NumberColumn("その前の相場", format="%d円/kg"),
                "change": st.column_config.NumberColumn("変化", format="%+.1f%%"),
            })

    items = sorted(df.loc[~df["item"].str.endswith("計"), "item"].unique())
    default = items.index("キャベツ") if "キャベツ" in items else 0
    item = st.selectbox("相場の推移を見る品目", items, index=default, key="wholesale_item")
    one = df[df["item"] == item].sort_values("date")
    st.plotly_chart(ch.daily_line(one, "date", "price_per_kg", height=240, name=item, unit="円/kg"),
                    width="stretch")
    st.download_button(
        f"{item}の相場をCSVでダウンロード（交渉資料用）",
        one[["date", "market", "item", "price_per_kg", "quantity_kg"]].to_csv(index=False).encode("utf-8-sig"),
        file_name=f"wholesale_{item}.csv", mime="text/csv", key="dl_wholesale")


# ---------------------------------------------------------------------------
# ③天気
# ---------------------------------------------------------------------------
def _section_weather() -> None:
    st.header("金沢の天気")
    df = crawl_store.load_weather("KNZ")
    if df.empty:
        st.info("天気はまだ集めていません。毎朝の自動収集（scripts/collect.py）で貯まります。")
        return
    today = pd.Timestamp.now(tz="Asia/Tokyo").strftime("%Y-%m-%d")
    coming = df[df["date"] >= today].head(3)
    cols = st.columns(max(len(coming), 1))
    for col, (_, r) in zip(cols, coming.iterrows()):
        diff = r["temp_diff_prev_day"]
        col.metric(f"{r['date'][5:]}　{r['weather'] or ''}", f"{r['max_temp']:.1f}℃",
                   None if pd.isna(diff) else f"{diff:+.1f}℃（前日比）", delta_color="off")
        col.caption(f"最低 {r['min_temp']:.1f}℃")
    st.caption(f"大きい数字が最高気温。Open-Meteo の予報（{df['fetched_at'].max()} 取得）。"
               "過去の実績は「データ基盤」画面で見られます。")
