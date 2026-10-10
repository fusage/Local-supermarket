# -*- coding: utf-8 -*-
"""データ基盤（外部データがきちんと貯まっているかを見る画面）

  毎朝 GitHub Actions が scripts/collect.py を実行 → data/raw/<種類>/<日付>.csv をコミット
  → アプリが未取り込みのファイルだけ統合DBに追記（lib/integrate.py の sync_raw）

  ① 収集の状況     … 種類ごとの最終取得・鮮度・件数
  ② 貯まったデータ … 天気の推移、競合特売の価格の記録
  ③ 取り込み記録   … _ingest_log
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from lib import charts as ch
from lib import crawl_store, gcp
from lib.integrate import RAW_SOURCES

LABELS = {"weather": "天気", "events": "地域のイベント", "flyers": "競合チラシ", "featured": "競合の特売商品",
          "market": "卸売相場（金沢）"}
STALE_DAYS = 2          # これより古ければ「更新が止まっている」とみなす


def _status_table() -> pd.DataFrame:
    log = crawl_store.ingest_log()
    now = pd.Timestamp.now(tz="Asia/Tokyo").tz_localize(None)
    rows = []
    for src, table in RAW_SOURCES.items():
        hist = crawl_store.history_counts(table)
        last = crawl_store.last_fetched(table)
        files = log[log["source"] == src] if not log.empty else log
        if last is None:
            state, age = "❌ まだ無い", None
        else:
            age = (now - pd.Timestamp(last)).total_seconds() / 86400
            state = "✅ 最新" if age < STALE_DAYS else f"⚠️ {int(age)}日更新なし"
        rows.append({
            "種類": LABELS.get(src, src), "テーブル": table, "状態": state, "最終取得": last,
            "取得した日数": len(hist), "累計件数": int(hist["rows"].sum()) if not hist.empty else 0,
            "生データファイル": len(files),
        })
    return pd.DataFrame(rows)


def _section_status() -> None:
    st.header("収集の状況")
    df = _status_table()
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"累計件数": st.column_config.NumberColumn(format="localized")})
    if df["状態"].str.startswith("✅").all():
        st.caption(f"すべての種類が {STALE_DAYS} 日以内に更新されています。")
    else:
        st.warning("更新が止まっている種類があります。GitHub の Actions タブで「collect」の実行結果を確認してください。")

    counts = []
    for src, table in RAW_SOURCES.items():
        h = crawl_store.history_counts(table)
        if not h.empty:
            counts.append(h.assign(種類=LABELS.get(src, src)))
    if counts:
        hist = pd.concat(counts)
        st.markdown("##### 取得日ごとの件数")
        fig = ch.multi_lines(hist, "fetched_date", "rows", "種類", height=260, yfmt=",.0f")
        fig.update_xaxes(type="category")       # 取得した日だけを並べる（1日分でも目盛りが崩れない）
        st.plotly_chart(fig, width="stretch")
        st.caption("毎日ほぼ同じ件数なら正常です。急に0や半分になった日は、サイトの構造が変わった可能性があります。")


def _section_weather() -> None:
    st.markdown("##### 天気の記録（最高気温）")
    w = crawl_store.load_weather()
    if w.empty:
        st.info("天気はまだ集めていません。")
        return
    obs = w[w["is_forecast"] == 0]
    if obs.empty:
        st.info("まだ予報しかありません。翌日以降、実績が貯まっていきます。")
        return
    st.plotly_chart(ch.multi_lines(obs, "date", "max_temp", "region_name", height=280),
                    width="stretch")
    cold = obs[obs["temp_diff_prev_day"] <= -2.5]
    st.caption(
        f"{obs['date'].min()} 〜 {obs['date'].max()} の {obs['date'].nunique()} 日分（Open-Meteo）。"
        f"前日より2.5℃以上下がった日（データ班の「気温急変日」の基準）は {len(cold)} 件です。")
    if not cold.empty:
        st.dataframe(
            cold[["date", "region_name", "weather", "max_temp", "temp_diff_prev_day"]].rename(columns={
                "date": "日付", "region_name": "地域", "weather": "天気",
                "max_temp": "最高気温", "temp_diff_prev_day": "前日差"}),
            hide_index=True, width="stretch")


def _section_featured() -> None:
    st.markdown("##### 競合の特売商品の記録")
    df = crawl_store.featured_price_history()
    if df.empty:
        st.info("特売商品はまだ集めていません。")
        return
    # 同じ販売日の同じ商品を何度も取得しているので、1件にまとめてから数える
    df = df.drop_duplicates(["store_name", "product_name", "sale_date"], keep="last")
    summary = (df.groupby(["store_name", "product_name"], as_index=False)
                 .agg(特売日数=("sale_date", "nunique"), 最安=("tax_price", "min"),
                      最高=("tax_price", "max"), 最初=("sale_date", "min"), 最後=("sale_date", "max"))
                 .sort_values(["特売日数", "最後"], ascending=False))
    st.dataframe(
        summary.rename(columns={"store_name": "店舗", "product_name": "商品名"}),
        hide_index=True, width="stretch",
        column_config={"最安": st.column_config.NumberColumn("最安(税込)", format="%d円"),
                       "最高": st.column_config.NumberColumn("最高(税込)", format="%d円")})
    st.caption("どの商品を、どのくらいの頻度・価格で特売しているか。日がたつほど傾向が見えてきます。")


def render() -> None:
    st.subheader("データ基盤")
    st.caption("外部のデータを毎朝自動で集めて貯めている、その状況を確認する画面です。")
    if gcp.enabled():
        s = gcp.settings()
        st.success(f"GCP で動作中：BigQuery `{s['project']}.{s['dataset']}` ／ "
                   f"Cloud Storage `gs://{s['bucket']}/raw/`", icon="☁️")
        st.code(
            "① Cloud Scheduler（毎朝6時）が Cloud Run ジョブ（scripts/collect.py）を起動\n"
            "② Cloud Storage の raw/<種類>/<日付>.csv に書き出す（生データ）\n"
            "③ BigQuery の外部テーブル raw_* がそのCSVを読み、ビュー crawl_* / *_latest で画面へ",
            language=None)
        crawl_store.last_fetched("crawl_weather")      # つながるかどうかを先に確かめる
        if gcp.status():
            st.error("GCP の設定はありますが、BigQuery につながりません。"
                     "Secrets の鍵（[gcp_service_account]）と権限を確認してください（docs/gcp.md）。"
                     "「ロス分析」は手元の SQLite で表示しています。\n\n"
                     f"詳細: {gcp.status()}", icon="⚠️")
            return
    else:
        st.code(
            "① GitHub Actions（毎朝6時）が scripts/collect.py を実行\n"
            "② data/raw/<種類>/<日付>.csv に書き出してコミット\n"
            "③ アプリが、まだ取り込んでいないファイルだけ統合DB（crawl_*）に追記",
            language=None)

    _section_status()
    st.header("貯まったデータ")
    _section_weather()
    _section_featured()

    st.header("取り込み記録")
    log = crawl_store.ingest_log()
    if log.empty:
        st.info("まだ生データを取り込んでいません。")
    else:
        st.dataframe(log.rename(columns={"path": "ファイル", "source": "種類", "rows": "行数",
                                         "loaded_at": "取り込んだ日時"}),
                     hide_index=True, width="stretch")
        st.caption("data/raw には1日1ファイルずつ足していき、過去の日のファイルは書き換えません。"
                   "DBを作り直しても、ここにあるファイルから同じ履歴が復元されます。")
