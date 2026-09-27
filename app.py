# =============================================================
# app.py — 統合ダッシュボード v0.1
# =============================================================
import json
import streamlit as st

from analysis import total, sum_by, search_records   # ← 集計と検索の関数

# ── ② データの読み書き関数 ──
@st.cache_data
def load_data():
    with open("sales_v1.json", "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(records):
    with open("sales_v1.json", "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


# ── ③ ページ設定・タイトル ──
st.set_page_config(page_title="統合ダッシュボード v0.1", page_icon="🛒")
st.title("🛒 統合ダッシュボード v0.1")
st.caption("売上データダッシュボード")

# ── ④ タブを作る ──
tab1, tab2, tab3 = st.tabs(["📊 ダッシュボード", "📝 登録", "📋 一覧"])
records = load_data()

# ── ⑤ ダッシュボードタブ（w1 の検索タブを置きかえた部分） ──
with tab1:
    st.subheader("📊 売上ダッシュボード")

    # 売上高
    st.metric("売上の合計", f"{total(records):,} 円")

    # 店舗ごとの売上グラフ
    st.markdown("#### 店舗ごとの売上")
    by_store = sum_by(records, "store")
    st.bar_chart(
        {"店舗": list(by_store.keys()), "売上": list(by_store.values())},
        x="店舗", y="売上"
    )

    st.caption(f"データ件数：{len(records)} 件")

# ── ⑥ 登録タブ ──
with tab2:
    st.subheader("📝 売上データを登録")

    # 登録が完了していたらメッセージを出す
    if st.session_state.get("registered"):
        st.success("登録完了！")
        st.session_state["registered"] = False

    with st.form("register_form"):
        date_str = st.text_input("日付（例：2026-09-24）")
        store    = st.selectbox("店舗", sorted({r["store"] for r in records}))
        product  = st.text_input("商品名")
        dept     = st.selectbox("部門", sorted({r["dept"] for r in records}))
        qty      = st.number_input("販売数", min_value=0, value=10)
        price    = st.number_input("売価", min_value=0, value=198)
        submitted = st.form_submit_button("登録する")

    if submitted:
        if not date_str or not product:
            st.error("日付と商品名は必須です")
        else:
            new_record = {
                "id"     : len(records) + 1,
                "date"   : date_str,
                "store"  : store,
                "product": product,
                "dept"   : dept,
                "qty"    : int(qty),
                "price"  : int(price),
            }
            records.append(new_record)
            save_data(records)                      # json.dump でファイルに保存
            st.cache_data.clear()                   # 古いキャッシュを捨てる
            st.session_state["registered"] = True   # 「登録した」印を残す
            st.rerun()                              # 画面を作り直す

# ── ⑦ 一覧タブ ──
with tab3:
    st.subheader(f"📋 売上データ一覧（{len(records)} 件）")

    query = st.text_input("🔍 商品名・部門・店舗名で検索", placeholder="例：牛乳")

    if query:
        shown = search_records(query, records)     
        st.markdown(f"**検索結果：{len(shown)} 件**")
    else:
        shown = records

    for r in shown:
        st.markdown(f"**{r['product']}**（{r['dept']}）")
        col1, col2, col3 = st.columns(3)
        col1.caption(f"🏪 {r['store']}")
        col2.caption(f"📅 {r['date']}")
        col3.caption(f"💰 {r['qty'] * r['price']:,} 円（{r['qty']}点）")
        st.divider()
