# スーパー統合ダッシュボード v0.1（初版）

Tech0 PROJECT ZERO の **w1 のコードを元にして作った初版**です。
「w1に何を足せばスーパーのダッシュボードになるか」が分かる形にしてあります。

## 動かし方

```bash
cd app_v1
pip install -r requirements.txt
streamlit run app.py
```

## w1 から何を変えたか

| w1のファイル | v0.1 | やったこと |
|---|---|---|
| `pages_w1.json` | `sales_v1.json` | **中身を入れ替えただけ**。キーを `date / store / product / dept / qty / price` に |
| `search.py` | `analysis.py` | `search_pages()` を `search_records()` に（探す場所を変えただけ）。**`total()` と `sum_by()` を新しく追加** |
| `app.py` タブ1「検索」 | タブ1「ダッシュボード」 | **`st.metric` と `st.bar_chart` を追加**（ここが今回の新しいところ） |
| `app.py` タブ2「登録」 | タブ2「登録」 | フォームの項目を売上データに差し替え（仕組みは w1 のまま） |
| `app.py` タブ3「一覧」 | タブ3「一覧」 | 一覧に検索ボックスを付けた（w1の検索タブと一覧タブを1つに） |

**新しく覚えることは `st.metric` と `st.bar_chart` の2つだけ**です。

### 実際に足したコード

`analysis.py`（これだけ）

```python
def total(records):
    sales = 0
    for r in records:
        sales += r["qty"] * r["price"]
    return sales


def sum_by(records, key):
    result = {}
    for r in records:
        name = r[key]
        if name not in result:
            result[name] = 0
        result[name] += r["qty"] * r["price"]
    return result
```

`app.py` のダッシュボードタブ（これだけ）

```python
st.metric("売上の合計", f"{total(records):,} 円")

by_store = sum_by(records, "store")
st.bar_chart(
    {"店舗": list(by_store.keys()), "売上": list(by_store.values())},
    x="店舗", y="売上"
)
```

## ファイル

| ファイル | 行数 | 中身 |
|---|---|---|
| `app.py` | 約110行 | 画面（ダッシュボード／登録／一覧の3タブ） |
| `analysis.py` | 約50行 | 合計する・まとめる・さがす |
| `sales_v1.json` | 84件 | 売上データ（7日 × 3店舗 × 4商品） |

データ1件はこの形です。

```json
{
  "id": 1,
  "date": "2026-09-18",
  "store": "本店",
  "product": "キャベツ",
  "dept": "青果",
  "qty": 33,
  "price": 198
}
```

売上は `qty × price` で計算して出します（合計をデータに持たない）。

## 次に何を足すか（アジャイルで育てる）

1回の打ち合わせで1つずつ足していくイメージです。

| 版 | 足すもの | 必要な追加 |
|---|---|---|
| v0.2 | **原価 `cost` を足して、粗利・粗利率を出す** | データに1項目、関数を1つ |
| v0.3 | **部門ごと・日ごとのグラフ** | `sum_by(records, "dept")` を呼ぶだけ（関数は既にある） |
| v0.4 | **店舗をえらぶサイドバー** | `st.sidebar.selectbox` と絞り込み関数 |
| v0.5 | **廃棄 `waste_qty` を足して、ロス率・発注の目安** | データに1項目、関数を2つ |
| v0.6 | **JSON → SQLite に置きかえ** | `schema.sql` と `database.py`（w3・w4でやった形） |
| v0.7 | 競合価格のクローラー、AIコメント | w4 の `crawler.py` の形 |

v0.6 の姿は `app_simple/` フォルダに、さらに作り込んだ姿は `app/` フォルダにあります。
迷ったらそちらを見てください。

## ダミーデータに入れてあること

- 土日の売上が多い（曜日で売れ方が変わる）
- 北町店の惣菜は売れ行きが落ちている（v0.5 で廃棄を足すと、ロス率の話につながります）
