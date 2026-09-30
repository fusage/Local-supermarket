# -*- coding: utf-8 -*-
"""地方独立系スーパー向け 統合ダッシュボード（フロントエンド）

役割分担（第5回打ち合わせ 2026/9/23）
  A フロントエンド（このファイル）… ヨッシー
  B データモデル・ダミーデータ      … 藤井さん  → data/supermarket.db を置けば自動で切り替わります
  C 検索・クローラー                … tomさん   → lib/search_engine.py を置けば自動で読み込みます

3人の成果物を1つにまとめた統合版です。画面はサイドバー上部のメニューで切り替えます。
  統合ダッシュボード … このファイル（経営／バイヤー／部門担当）
  ロス分析           … views/loss.py   （データ班のテーブルと分析ビュー）
  競合・地域情報     … views/market.py （クローラー）

起動方法:  streamlit run app.py
"""
from __future__ import annotations

import threading
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from lib import charts as ch
from lib import crawl_store, db, integrate
from views import loss, market

# --- 他メンバーのモジュールがあれば使う（無くても動く） --------------------
try:                                   # tomさん担当：自然言語→SQLの検索エンジン
    from lib import search_engine      # 期待する関数: search(query:str) -> pandas.DataFrame | dict
except Exception:
    search_engine = None

try:                                   # 生成AIによる分析コメント（任意）
    from lib import ai_comment         # 期待する関数: comment(context:dict) -> str
except Exception:
    ai_comment = None


st.set_page_config(
    page_title="統合ダッシュボード｜地方独立系スーパー",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)

ROLES = {
    "経営": "経営ダッシュボード",
    "バイヤー": "バイヤーダッシュボード",
    "部門担当": "部門担当ダッシュボード",
}


# ==========================================================================
# データの準備
# ==========================================================================
@st.cache_resource(show_spinner=False)
def _build_lock() -> threading.Lock:
    """DBを作る処理が、同時に開いた複数の画面から重ならないようにするための鍵。"""
    return threading.Lock()


def ensure_database() -> str:
    """DBが無ければ暫定ダミーデータを生成し、データ班・クローラーのテーブルを統合する。"""
    path, kind = db.resolve_db_path()
    with _build_lock():
        if not path.exists():
            with st.status("初回のみ：暫定ダミーデータを生成しています（30秒ほど）", expanded=True) as s:
                bar = st.progress(0.0)
                from lib import sample_data

                def _p(msg: str, ratio: float):
                    s.write(msg)
                    bar.progress(ratio)

                # 作りかけのファイルを読まれないよう、別名で作ってから置き換える
                tmp = path.with_name(path.name + ".building")
                sample_data.generate(tmp, progress=_p)
                integrate.ensure(tmp)
                tmp.replace(path)
                s.update(label="ダミーデータの生成が完了しました", state="complete")
        integrate.ensure(path)      # 足りないテーブルだけ作る（2回目以降は何もしない）
    return kind


# ==========================================================================
# サイドバー（共通の絞り込み）
# ==========================================================================
def sidebar(kind: str) -> dict:
    st.sidebar.title("🛒 統合ダッシュボード")

    if kind == "real":
        st.sidebar.success("本番データ（data/supermarket.db）を読み込み中", icon="✅")
    else:
        st.sidebar.warning("暫定ダミーデータで動作中（藤井さんの本番データ待ち）", icon="⚠️")

    role = st.sidebar.radio("見る立場", list(ROLES.keys()), index=0,
                            help="立場ごとに見たい指標が違うため、画面を切り替えます")

    dmin, dmax = db.date_bounds()
    dmax_d = pd.Timestamp(dmax).date()
    dmin_d = pd.Timestamp(dmin).date()

    st.sidebar.subheader("期間")
    preset = st.sidebar.selectbox(
        "よく使う期間", ["直近12か月", "直近3か月", "直近1か月", "今年度（4月〜）", "カスタム"],
        index=0 if role != "部門担当" else 2,
    )
    if preset == "直近12か月":
        start, end = dmax_d - timedelta(days=364), dmax_d
    elif preset == "直近3か月":
        start, end = dmax_d - timedelta(days=89), dmax_d
    elif preset == "直近1か月":
        start, end = dmax_d - timedelta(days=29), dmax_d
    elif preset == "今年度（4月〜）":
        fy = dmax_d.year if dmax_d.month >= 4 else dmax_d.year - 1
        start, end = date(fy, 4, 1), dmax_d
    else:
        picked = st.sidebar.date_input(
            "期間を指定", (dmax_d - timedelta(days=89), dmax_d),
            min_value=dmin_d, max_value=dmax_d)
        # 開始日だけ選んだ状態（終了日を選ぶ前）でも落ちないようにする
        if isinstance(picked, (list, tuple)):
            start = picked[0]
            end = picked[1] if len(picked) > 1 else dmax_d
        else:
            start, end = picked, dmax_d
    st.sidebar.caption(f"{start} 〜 {end}")

    m = db.masters()
    stores, depts = m["stores"], m["departments"]

    st.sidebar.subheader("絞り込み")
    if role == "部門担当":
        store_name = st.sidebar.selectbox("店舗", stores["store_name"])
        dept_name = st.sidebar.selectbox("部門", depts["dept_name"])
        store_ids = (int(stores.loc[stores["store_name"] == store_name, "store_id"].iloc[0]),)
        dept_ids = (int(depts.loc[depts["dept_name"] == dept_name, "dept_id"].iloc[0]),)
    else:
        sel_s = st.sidebar.multiselect("店舗（未選択＝全店）", stores["store_name"])
        sel_d = st.sidebar.multiselect("部門（未選択＝全部門）", depts["dept_name"])
        store_ids = tuple(int(x) for x in
                          stores.loc[stores["store_name"].isin(sel_s), "store_id"])
        dept_ids = tuple(int(x) for x in
                         depts.loc[depts["dept_name"].isin(sel_d), "dept_id"])

    st.sidebar.divider()
    st.sidebar.caption(
        "データ最終更新：" + dmax + "\n\n"
        "※ 数値はすべて架空のダミーデータです。"
    )
    return {
        "role": role,
        "start": str(start),
        "end": str(end),
        "stores": store_ids,
        "depts": dept_ids,
        "masters": m,
        "max_date": dmax,
    }


# ==========================================================================
# 横断検索（要件定義 C-03）
# ==========================================================================
def search_panel(f: dict) -> None:
    col1, col2 = st.columns([5, 1])
    with col1:
        query = st.text_input(
            "検索", placeholder="例）バナナ　／　牛乳 在庫　／　24年度 売上",
            label_visibility="collapsed", key="search_query")
    with col2:
        clear = st.button("クリア", width="stretch")
    if clear:
        st.session_state["search_query"] = ""
        st.rerun()
    if not query:
        return

    with st.container(border=True):
        st.markdown(f"**「{query}」の検索結果**")

        # tomさん担当の検索エンジンがあればそちらを使う
        if search_engine is not None and hasattr(search_engine, "search"):
            try:
                result = search_engine.search(query)
                if isinstance(result, pd.DataFrame):
                    st.dataframe(result, width="stretch")
                else:
                    st.write(result)
                return
            except Exception as e:      # 検索側のエラーで画面全体を落とさない
                st.info(f"検索エンジンでエラーが出たため、簡易検索に切り替えました（{e}）")

        # --- 簡易検索（商品名・読み仮名・部門・カテゴリの部分一致） ---------
        hits = db.find_products(query)
        if hits.empty:
            st.write("該当する商品が見つかりませんでした。商品名・部門名・カテゴリで探せます。")
            return
        st.caption(f"{len(hits)}件ヒット（商品名・読み仮名・部門・カテゴリから検索）")
        pick = st.selectbox(
            "商品を選ぶと売上の推移を表示します",
            hits["product_name"], key="search_pick")
        pid = int(hits.loc[hits["product_name"] == pick, "product_id"].iloc[0])
        trend = db.product_sales_trend(pid, f["start"], f["end"], f["stores"])
        if trend.empty:
            st.write("この期間の販売実績がありません。")
            return
        c1, c2, c3 = st.columns(3)
        c1.metric("期間売上", ch.yen(trend["amount"].sum()))
        c2.metric("販売数", f'{trend["qty"].sum():,.0f} 点')
        c3.metric("粗利率", ch.pct(trend["gp"].sum() / trend["amount"].sum() * 100))
        st.plotly_chart(
            ch.trend_lines(trend, "ym", [("amount", "売上")], height=240, unit="万円"),
            width="stretch")
        st.dataframe(hits, width="stretch", hide_index=True)


# ==========================================================================
# 自動コメント（AI連携の差し込み口）
# ==========================================================================
def auto_comment(context: dict) -> str:
    """数値から要点を文章にする。

    いまはルールベース。生成AIを使う場合は lib/ai_comment.py に
    comment(context: dict) -> str を用意すれば、そちらが呼ばれます（要件定義 C-05）。
    """
    if ai_comment is not None and hasattr(ai_comment, "comment"):
        try:
            return ai_comment.comment(context)
        except Exception as e:
            return f"（AIコメントの生成に失敗したため、簡易コメントを表示します：{e}）"

    k = context["kpi"]
    lines = []
    yoy = (k["amount"] / k["prev_amount"] - 1) * 100 if k["prev_amount"] else 0
    bud = (k["amount"] / k["budget_amount"] - 1) * 100 if k["budget_amount"] else 0
    lines.append(
        f"- 売上は {ch.yen(k['amount'])}（前年比 {yoy:+.1f}%、予算比 {bud:+.1f}%）、"
        f"粗利率は {k['gp_rate']:.1f}% です。"
    )
    st_df = context.get("by_store")
    if st_df is not None and not st_df.empty:
        worst = st_df.sort_values("yoy").iloc[0]
        best = st_df.sort_values("yoy").iloc[-1]
        lines.append(
            f"- 前年比がいちばん悪いのは **{worst['store_name']}**（{worst['yoy']:+.1f}%）、"
            f"いちばん良いのは {best['store_name']}（{best['yoy']:+.1f}%）です。"
        )
    dp = context.get("by_dept")
    if dp is not None and not dp.empty:
        w = dp.sort_values("yoy").iloc[0]
        lines.append(
            f"- 部門では **{w['dept_name']}** が前年比 {w['yoy']:+.1f}% と弱く、"
            f"粗利率は {w['gp_rate']:.1f}%、売上構成比は {w['share']:.1f}% です。"
        )
    ls = context.get("loss")
    if ls is not None and not ls.empty:
        t = ls.iloc[0]
        lines.append(
            f"- ロス率がいちばん高いのは **{t['store_name']}の{t['dept_name']}**"
            f"（{t['loss_rate']:.1f}%、廃棄 {ch.yen(t['waste'])}）です。"
            "　作業手順と値引きのタイミングを確認してください。"
        )
    lines.append("- ※ 最終的な判断は人が行ってください。この文章は数値から機械的に作ったものです。")
    return "\n".join(lines)


# ==========================================================================
# ① 経営ダッシュボード
# ==========================================================================
def page_management(f: dict) -> None:
    st.subheader("経営ダッシュボード")
    st.caption("全社の業績をつかみ、手を打つべき店舗・部門を見つける画面です。")

    k = db.kpi(f["start"], f["end"], f["stores"], f["depts"])
    yoy = (k["amount"] / k["prev_amount"] - 1) * 100 if k["prev_amount"] else None
    gp_yoy = (k["gross_profit"] / k["prev_gp"] - 1) * 100 if k["prev_gp"] else None
    bud = (k["amount"] / k["budget_amount"] - 1) * 100 if k["budget_amount"] else None

    c = st.columns(5)
    c[0].metric("売上（億円）", f'{k["amount"]/1e8:,.1f}',
                ch.pct(yoy, sign=True) + "（前年比）" if yoy is not None else None)
    c[1].metric("粗利（億円）", f'{k["gross_profit"]/1e8:,.1f}',
                ch.pct(gp_yoy, sign=True) + "（前年比）" if gp_yoy is not None else None)
    c[2].metric("粗利率", ch.pct(k["gp_rate"]))
    c[3].metric("客数（万人）", f'{k["customers"]/10000:,.1f}')
    c[4].metric("客単価（円）", f'{k["spend"]:,.0f}')
    if bud is not None:
        badge = "🟢" if bud >= 0 else "🔴"
        st.caption(f"{badge} 予算比 {bud:+.1f}%（予算 {ch.yen(k['budget_amount'])}）")

    st.markdown("##### 売上の推移（月次）")
    tr = db.monthly_trend(f["start"], f["end"], f["stores"], f["depts"])
    st.plotly_chart(
        ch.trend_lines(tr, "ym", [("amount", "売上"), ("prev_amount", "前年"), ("budget", "予算")]),
        width="stretch")

    left, right = st.columns(2)
    bs = db.by_store(f["start"], f["end"], f["depts"])
    with left:
        st.markdown("##### 店舗別の売上")
        st.plotly_chart(
            ch.ranking_bar(bs, "store_name", "amount",
                           text=[ch.yen(v) for v in bs.sort_values("amount")["amount"]]),
            width="stretch")
    with right:
        st.markdown("##### 店舗別の前年比")
        st.plotly_chart(ch.diverging_bar(bs, "store_name", "yoy"), width="stretch")
        st.caption("赤＝前年割れ。まずここから原因を確認します。")

    st.markdown("##### 部門別の状況")
    bd = db.by_dept(f["start"], f["end"], f["stores"])
    lc, rc = st.columns([3, 2])
    with lc:
        st.plotly_chart(
            ch.grouped_bar(bd, "dept_name", [("amount", "今年"), ("prev_amount", "前年")]),
            width="stretch")
    with rc:
        st.dataframe(
            bd[["dept_name", "amount", "share", "gp_rate", "yoy"]],
            hide_index=True, width="stretch",
            column_config={
                "dept_name": "部門",
                "amount": st.column_config.NumberColumn("売上", format="localized"),
                "share": st.column_config.NumberColumn("構成比", format="%.1f%%"),
                "gp_rate": st.column_config.NumberColumn("粗利率", format="%.1f%%"),
                "yoy": st.column_config.NumberColumn("前年比", format="%+.1f%%"),
            })

    st.markdown("##### ロス・欠品（店舗×部門）")
    ls = db.loss_summary(f["start"], f["end"], f["stores"], f["depts"])
    top = ls.head(10).copy()
    top["label"] = top["store_name"] + "／" + top["dept_name"]
    lc2, rc2 = st.columns([3, 2])
    with lc2:
        st.plotly_chart(
            ch.ranking_bar(top, "label", "loss_rate",
                           text=[f"{v:.1f}%" for v in top.sort_values("loss_rate")["loss_rate"]],
                           color=ch.STATUS["serious"]),
            width="stretch")
        st.caption("ロス率＝廃棄金額 ÷ 売上。上位から手を打ちます。")
    with rc2:
        st.dataframe(
            top[["label", "waste", "loss_rate", "stockouts"]],
            hide_index=True, width="stretch",
            column_config={
                "label": "店舗／部門",
                "waste": st.column_config.NumberColumn("廃棄額", format="localized"),
                "loss_rate": st.column_config.NumberColumn("ロス率", format="%.1f%%"),
                "stockouts": st.column_config.NumberColumn("欠品件数", format="localized"),
            })

    with st.expander("🤖 今月の要点（自動コメント）", expanded=True):
        st.markdown(auto_comment({"kpi": k, "by_store": bs, "by_dept": bd, "loss": ls}))


# ==========================================================================
# ② バイヤーダッシュボード
# ==========================================================================
def page_buyer(f: dict) -> None:
    st.subheader("バイヤーダッシュボード")
    st.caption("競合価格と売れ行きをもとに、仕入・価格・品揃えを決める画面です。")

    perf = db.product_perf(f["start"], f["end"], f["stores"], f["depts"])
    if perf.empty:
        st.info("この条件に該当する販売実績がありません。")
        return

    c = st.columns(4)
    c[0].metric("対象商品数", f"{len(perf):,} 品目")
    c[1].metric("売上", ch.yen(perf["amount"].sum()))
    c[2].metric("粗利率", ch.pct(perf["gp"].sum() / perf["amount"].sum() * 100))
    down = perf[perf["yoy"] < 0]
    c[3].metric("前年割れの商品", f"{len(down):,} 品目",
                f"{len(down)/len(perf)*100:.0f}% が前年割れ", delta_color="off")

    tab1, tab2, tab3 = st.tabs(["商品別の実績", "ABC分析", "競合価格との比較"])

    with tab1:
        st.dataframe(
            perf[["product_name", "dept_name", "category", "qty", "amount", "gp_rate", "yoy", "std_price"]],
            hide_index=True, width="stretch", height=430,
            column_config={
                "product_name": "商品名",
                "dept_name": "部門",
                "category": "カテゴリ",
                "qty": st.column_config.NumberColumn("販売数", format="localized"),
                "amount": st.column_config.NumberColumn("売上", format="localized"),
                "gp_rate": st.column_config.NumberColumn("粗利率", format="%.1f%%"),
                "yoy": st.column_config.NumberColumn("前年比", format="%+.1f%%"),
                "std_price": st.column_config.NumberColumn("標準売価", format="localized"),
            })
        st.caption("列名をクリックすると並べ替えできます。")

    with tab2:
        abc = db.abc_analysis(perf, "amount")
        counts = abc["rank_class"].value_counts()
        cc = st.columns(3)
        for i, r in enumerate(["A", "B", "C"]):
            cc[i].metric(f"{r}ランク", f"{int(counts.get(r, 0)):,} 品目")
        top25 = abc.head(25)
        st.plotly_chart(ch.class_bar(top25, "product_name", "amount", "rank_class", height=520),
                        width="stretch")
        st.caption("A＝売上上位70%、B＝〜90%、C＝残り。Cランクは見直しの候補です。")
        st.dataframe(
            abc[abc["rank_class"].astype(str) == "C"][
                ["product_name", "dept_name", "amount", "gp_rate", "yoy", "cum_share"]],
            hide_index=True, width="stretch",
            column_config={
                "product_name": "商品名", "dept_name": "部門",
                "amount": st.column_config.NumberColumn("売上", format="localized"),
                "gp_rate": st.column_config.NumberColumn("粗利率", format="%.1f%%"),
                "yoy": st.column_config.NumberColumn("前年比", format="%+.1f%%"),
                "cum_share": st.column_config.NumberColumn("累計構成比", format="%.1f%%"),
            })

    with tab3:
        competitor_tab(f)


def crawled_featured_items() -> None:
    """クローラーが集めた競合の特売商品（「競合・地域情報」画面で取得・保存されたもの）。"""
    st.markdown("##### クローラーが集めた競合の特売商品")
    items = crawl_store.load_featured_items()
    if items.empty:
        st.info("まだ保存されていません。サイドバーの「競合・地域情報」を開くと取得・保存されます。")
        return
    st.caption(
        f"実在の競合店がウェブに掲載している特売商品です（{items['fetched_at'].max()} 取得）。"
        "上のグラフ（ダミーの競合価格）とは別のデータで、自社商品との対応づけはまだ行っていません。")
    st.dataframe(
        items[["store_name", "sale_date", "product_name", "spec", "base_price", "tax_price", "product_url"]],
        hide_index=True, width="stretch", height=300,
        column_config={
            "store_name": "競合店", "sale_date": "日付", "product_name": "商品名", "spec": "内容",
            "base_price": st.column_config.NumberColumn("本体価格", format="localized"),
            "tax_price": st.column_config.NumberColumn("税込価格", format="localized"),
            "product_url": st.column_config.LinkColumn("商品ページ", display_text="開く"),
        })


def competitor_tab(f: dict) -> None:
    if not db.table_exists("competitor_prices"):
        st.info("競合価格のデータがまだありません（tomさん担当のクローラーで収集予定）。")
    else:
        cg = db.competitor_gap(f["depts"])
        if cg.empty:
            st.info("この条件に該当する競合価格データがありません。")
        else:
            competitor_gap_section(cg)
    crawled_featured_items()


def competitor_gap_section(cg: pd.DataFrame) -> None:
    low = (cg.groupby(["product_name", "dept_name", "std_price"], as_index=False)
             .agg(min_price=("price", "min")))
    low["gap"] = low["std_price"] - low["min_price"]
    low["gap_rate"] = low["gap"] / low["std_price"] * 100
    lose = low[low["gap"] > 0]
    m1, m2 = st.columns(2)
    m1.metric("自社が高い商品", f"{len(lose):,} / {len(low):,} 品目")
    m2.metric("平均の価格差", f"{lose['gap'].mean():,.0f} 円" if len(lose) else "―")
    st.plotly_chart(
        ch.diverging_bar(low.sort_values("gap", ascending=False).head(15),
                         "product_name", "gap_rate", height=440,
                         good_is_positive=False),
        width="stretch")
    st.caption("赤＝競合より自社が高い商品。プラスが大きいほど価格差が大きいことを示します。")
    st.dataframe(
        cg.sort_values(["product_name", "price"])[
            ["product_name", "dept_name", "std_price", "competitor_name", "business_type", "price", "fetched_at"]],
        hide_index=True, width="stretch", height=300,
        column_config={
            "product_name": "商品名", "dept_name": "部門",
            "std_price": st.column_config.NumberColumn("自社売価", format="localized"),
            "competitor_name": "競合店", "business_type": "業態",
            "price": st.column_config.NumberColumn("競合価格", format="localized"),
            "fetched_at": "取得日時",
        })


# ==========================================================================
# ③ 部門担当者ダッシュボード
# ==========================================================================
def page_department(f: dict) -> None:
    store_id = f["stores"][0]
    dept_id = f["depts"][0]
    m = f["masters"]
    store_name = m["stores"].set_index("store_id").loc[store_id, "store_name"]
    dept_name = m["departments"].set_index("dept_id").loc[dept_id, "dept_name"]

    st.subheader(f"部門担当ダッシュボード｜{store_name}　{dept_name}")
    st.caption("今日・明日の発注と、売場の打ち手を決める画面です。")

    daily = db.daily_progress(f["start"], f["end"], store_id, dept_id)
    waste = db.waste_ranking(f["start"], f["end"], store_id, dept_id)
    so = db.stockout_detail(f["start"], f["end"], store_id, dept_id)

    amount = daily["amount"].sum() if not daily.empty else 0
    gp = daily["gp"].sum() if not daily.empty else 0
    waste_amt = waste["amount"].sum() if not waste.empty else 0
    c = st.columns(4)
    c[0].metric("期間売上", ch.yen(amount))
    c[1].metric("粗利率", ch.pct(gp / amount * 100 if amount else 0))
    c[2].metric("廃棄額", ch.yen(waste_amt),
                f"ロス率 {waste_amt/amount*100:.1f}%" if amount else None, delta_color="off")
    c[3].metric("欠品件数", f'{int(so["cnt"].sum()) if not so.empty else 0:,} 件')

    st.markdown("##### 日次の売上")
    if daily.empty:
        st.info("この期間の実績がありません。")
    else:
        st.plotly_chart(ch.daily_line(daily, "sales_date", "amount"), width="stretch")

    tab1, tab2, tab3, tab4 = st.tabs(["発注の目安", "廃棄", "欠品", "在庫"])

    with tab1:
        as_of = f["end"]
        rs = db.reorder_suggestion(as_of, store_id, dept_id)
        st.caption(
            f"基準日 {as_of}｜発注の目安 ＝ 過去4週の同じ曜日の平均販売数 × 1.1 − 現在庫"
            "（かんたんな計算です。需要予測は今回のスコープ外）")
        if rs.empty:
            st.info("計算に必要な実績がありません。")
        else:
            st.dataframe(
                rs[["product_name", "avg_qty", "stock_qty", "suggest"]],
                hide_index=True, width="stretch",
                column_config={
                    "product_name": "商品名",
                    "avg_qty": st.column_config.NumberColumn("同曜日の平均販売数", format="%.1f"),
                    "stock_qty": st.column_config.NumberColumn("現在庫", format="localized"),
                    "suggest": st.column_config.NumberColumn("発注の目安", format="localized"),
                })

    with tab2:
        if waste.empty:
            st.info("この期間の廃棄はありません。")
        else:
            st.plotly_chart(
                ch.ranking_bar(waste.head(10), "product_name", "amount",
                               text=[ch.yen(v) for v in waste.head(10).sort_values("amount")["amount"]],
                               color=ch.STATUS["serious"], height=360),
                width="stretch")
            st.dataframe(waste, hide_index=True, width="stretch",
                         column_config={
                             "product_name": "商品名",
                             "qty": st.column_config.NumberColumn("廃棄数", format="localized"),
                             "amount": st.column_config.NumberColumn("廃棄額", format="localized"),
                         })

    with tab3:
        if so.empty:
            st.info("この期間の欠品はありません。")
        else:
            by_slot = so.groupby("time_slot", as_index=False)["cnt"].sum()
            st.plotly_chart(
                ch.ranking_bar(by_slot, "time_slot", "cnt", height=240,
                               color=ch.STATUS["critical"]),
                width="stretch")
            st.caption("時間帯別の欠品件数。夕方に多いなら、昼の追加発注や陳列の見直しが効きます。")
            st.dataframe(so.head(20), hide_index=True, width="stretch",
                         column_config={"product_name": "商品名", "time_slot": "時間帯",
                                        "cnt": st.column_config.NumberColumn("件数", format="localized")})

    with tab4:
        inv = db.inventory_status(f["end"], store_id, dept_id)
        if inv.empty:
            st.info("在庫データがありません。")
        else:
            st.caption("在庫日数＝現在庫 ÷ 直近7日の平均販売数。数字が大きいほど在庫を持ちすぎです。")
            mx = inv["days"].max()
            mx = 5.0 if pd.isna(mx) else float(max(5.0, mx))
            st.dataframe(
                inv, hide_index=True, width="stretch",
                column_config={
                    "product_name": "商品名",
                    "stock_qty": st.column_config.NumberColumn("在庫数", format="localized"),
                    "avg_qty": st.column_config.NumberColumn("平均販売数/日", format="%.1f"),
                    "days": st.column_config.ProgressColumn(
                        "在庫日数", format="%.1f日", min_value=0.0, max_value=mx),
                })

    st.markdown("##### 売場づくり・対応のナレッジ")
    kcol1, kcol2 = st.columns([3, 1])
    with kcol1:
        kw = st.text_input("ナレッジを検索", placeholder="例）廃棄　欠品　POP",
                           key="kw_knowledge", label_visibility="collapsed")
    with kcol2:
        only_mine = st.checkbox("自部門のみ", value=True)
    kn = db.knowledge(kw, dept_id=dept_id if only_mine else None)
    if kn.empty:
        st.info("該当するナレッジがありません。")
    else:
        for _, r in kn.iterrows():
            with st.container(border=True):
                st.markdown(f"**{r['title']}**　　`{r['dept_name'] or '共通'}`　👍 {r['helpful_count']}")
                st.write(r["body"])
                st.caption(f"{r['author']}　{r['created_at']}　タグ：{r['tags']}")


# ==========================================================================
# メイン
# ==========================================================================
def page_dashboard() -> None:
    """統合ダッシュボード（経営／バイヤー／部門担当）。"""
    f = sidebar(db.resolve_db_path()[1])
    search_panel(f)
    if f["role"] == "経営":
        page_management(f)
    elif f["role"] == "バイヤー":
        page_buyer(f)
    else:
        page_department(f)


def main() -> None:
    ensure_database()
    nav = st.navigation([
        st.Page(page_dashboard, title="統合ダッシュボード", icon="🛒", default=True),
        st.Page(loss.render, title="ロス分析", icon="📉", url_path="loss"),
        st.Page(market.render, title="競合・地域情報", icon="📰", url_path="market"),
    ])
    nav.run()


if __name__ == "__main__":
    main()
