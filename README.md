# 統合ダッシュボード（統合版）

地方独立系スーパー向け統合ダッシュボードの Streamlit アプリです。
3人の成果物（画面・データ・クローラー）を **1つのアプリ・1つのデータベース** にまとめてあります。

| 画面（サイドバー上部で切り替え） | 中身 | 元の担当 |
|---|---|---|
| 🛒 統合ダッシュボード | 経営／バイヤー／部門担当の3つの立場、横断検索 | A フロントエンド |
| 📉 ロス分析 | 3大ロス（廃棄・値引・機会ロス）、気温急変日の発注検証、夕方の見切り候補 | B データ班 |
| 📰 競合・地域情報 | 競合店のチラシ・特売商品、チラシのOCR、金沢市の来月のイベント | C クローラー |

---

## 1. 動かし方

```bash
pip install -r requirements.txt
streamlit run app.py
```

初回起動時にデータベース（`data/supermarket_dummy.db`）を**自動で作ります**（数秒〜30秒）。
ブラウザで http://localhost:8501 が開きます。

> Windowsの注意：アプリを起動したままだと `data/*.db` を作り直せません（ファイルが使用中になります）。
> データを作り直すときは、いったんアプリを止めてください。

---

## 2. データの統合のしくみ

データベースは SQLite のファイル1つです。中に3系統のテーブルが入っています。

| 系統 | テーブル | どこから来るか |
|---|---|---|
| A 画面用（ER図のテーブル） | `stores` `departments` `products` `sales` `inventory` `waste` `stockouts` `purchases` `budgets` `customers_daily` `competitors` `competitor_prices` `knowledge` ほか | `lib/sample_data.py` が生成（暫定ダミー）。`data/supermarket.db` を置けばそちらを優先 |
| B データ班 | `M_STORE` `M_PRODUCT` `M_WEATHER` `T_SALES` `T_ORDER` `T_INVENTORY` `T_WASTE_DISCOUNT` ＋ ビュー `V_STORE_LOSS_SUMMARY` `V_WEATHER_HYPOTHESIS_CHECK` `V_EVENING_DISCOUNT_CANDIDATES` | `data/csv/*.csv` を取り込み、`scripts/analysis_views.sql` でビューを作成 |
| C クローラー | `crawl_flyers` `crawl_featured_items` `crawl_events` | 「競合・地域情報」画面を開いたときに取得して保存（最新の1回ぶん） |

統合は `lib/integrate.py` が行います。**足りないテーブルだけ作る**ので、起動のたびに呼ばれても害はありません。

手元で作り直すとき：

```bash
python scripts/build_db.py           # 足りないものだけ作る（テーブルと件数の一覧も出ます）
python scripts/build_db.py --csv     # data/csv/*.csv を読み直す（CSVを更新したとき）
python scripts/build_db.py --all     # 暫定ダミーデータから全部作り直す
```

### まだ統合していないこと（チームで決めたいこと）

- **店舗・商品のマスタは2系統のままです。**
  画面用は8店舗（本店・駅前店…）・48商品・2年分、データ班は5店舗（サンフジ中央店…）・12商品・7日分で、
  店舗名も商品コードも対応していません。そのため「ロス分析」は別画面にしてあり、統合ダッシュボードの店舗・期間の絞り込みは効きません。
  どちらのマスタに寄せるかを決めれば、1本化できます。
- **クローラーの特売商品と、自社商品の対応づけは未実装です。**
  バイヤー画面の「競合価格との比較」のグラフはダミーの競合価格（`competitor_prices`）のままで、
  クローラーが集めた実データはその下に一覧で出しています。
- 舞台の地域も、画面用ダミーは架空の市、データ班は富山市、クローラーは金沢市と、そろっていません。

---

## 3. ファイル構成

| ファイル | 中身 | 元の担当 |
|---|---|---|
| `app.py` | 入口。画面の切り替えと、統合ダッシュボード（サイドバー・検索・3つの立場） | A |
| `views/loss.py` | ロス分析の画面 | B（画面は統合時に作成） |
| `views/market.py` | 競合・地域情報の画面 | C |
| `lib/db.py` | DB接続と集計クエリ。**画面から使うデータの窓口** | A |
| `lib/charts.py` | グラフの共通設定（配色・書式） | A |
| `lib/sample_data.py` | 暫定ダミーデータ生成 | A（仮） |
| `lib/paths.py` | ファイルの置き場所（DB・CSV） | 統合 |
| `lib/integrate.py` | データの統合（CSV取り込み・ビュー作成・クローラー用テーブル） | 統合 |
| `lib/crawl_store.py` | クローラーの取得結果をDBに保存・読み出し | 統合 |
| `lib/crawler/competitor_flyer_scraper.py` | 競合チラシ・特売商品の取得（イオン／トクバイ） | C |
| `lib/crawler/flyer_item_extractor.py` | チラシ画像のOCR（Tesseract） | C |
| `lib/crawler/kanazawa_events_scraper.py` | 金沢市イベント情報の取得 | C |
| `data/csv/*.csv` | データ班のマスタ・トランザクション（7ファイル） | B |
| `scripts/create_csv.py` `scripts/create_transactions.py` | 上のCSVを作るスクリプト | B |
| `scripts/analysis_views.sql` | 分析ビュー3本の定義 | B |
| `scripts/build_db.py` | 統合DBを手元で作る／作り直す | 統合 |
| `requirements.txt` / `packages.txt` | Pythonライブラリ／OSのパッケージ（OCR用） | 統合 |
| `.streamlit/config.toml` | 画面の配色テーマ | A |

元のフォルダから**持ってこなかったもの**：
データ班の `app.py` `analysis.py` `sales_v1.json` `pages/1_Dashboard.py`（v0.1の画面。`views/loss.py` に置き換え）、
`config.py` `scripts/csv_to_db.py` `scripts/run_analysis.py` `modules/db_connector.py`（`lib/paths.py` `lib/integrate.py` `lib/db.py` に統合）、
クローラーの `app.py`（`views/market.py` に移植）。

---

## 4. Streamlit Community Cloud で公開する

1. このフォルダを GitHub のリポジトリに上げる
2. https://share.streamlit.io で「Create app」→ リポジトリとブランチを選び、Main file path に `app.py` を指定
3. Deploy

- DBファイルは GitHub に上げません（`.gitignore` 済み）。Cloud 上でも初回アクセス時に自動生成されます。
- チラシのOCRに必要な Tesseract は `packages.txt` で自動的に入ります。
- Cloud はしばらくアクセスが無いと停止し、再開時にDBを作り直します。
  そのときクローラーの保存ぶん（`crawl_*`）は消えますが、「競合・地域情報」を開けば取り直します。
- APIキーを使う場合は Cloud の「Secrets」に設定し、`.streamlit/secrets.toml` は **GitHub に上げない**でください。

手元でOCRを使うには、別途 Tesseract 本体のインストールが必要です
（Windows: https://github.com/UB-Mannheim/tesseract/wiki のインストーラで「Japanese」を追加）。
無くてもアプリは動き、OCRのボタンを押したときに案内が出ます。

---

## 5. 各担当の差し込み口（統合前から変わっていません）

### 画面用の本番データ（B）
`data/supermarket.db` にSQLiteファイルを置くだけで切り替わります
（サイドバーの表示が「本番データを読み込み中」に変わります）。
テーブル名・列名は **ER図（要件定義書 5.1.1）のとおり**にしてください。画面が参照している列は次のとおりです。

| テーブル | 必須の列 |
|---|---|
| `stores` | store_id, store_name, area, floor_m2, opened_year |
| `departments` | dept_id, dept_name |
| `products` | product_id, product_name, kana, dept_id, category, std_price, std_cost, jan_code |
| `suppliers` | supplier_id, supplier_name |
| `sales` | sales_date, store_id, product_id, qty, amount, gross_profit, is_promo |
| `inventory` | inv_date, store_id, product_id, stock_qty |
| `purchases` | purchase_date, store_id, product_id, supplier_id, purchase_qty, unit_cost |
| `waste` | waste_date, store_id, product_id, waste_qty, waste_amount |
| `stockouts` | stockout_date, time_slot, store_id, product_id |
| `budgets` | year_month（'2026-09'形式）, store_id, dept_id, sales_budget, gp_budget |
| `customers_daily` | visit_date, store_id, customer_count, member_count |
| `competitors` | competitor_id, competitor_name, business_type, near_store_id |
| `competitor_prices` | price_id, fetched_at, competitor_id, raw_name, product_id, price, source_url |
| `knowledge` | knowledge_id, title, body, dept_id, tags, photo_path, author, created_at, helpful_count |

決めごと：

- **日付はすべて `'YYYY-MM-DD'` の文字列**（SQLiteにDATE型が無いため）。`budgets.year_month` だけ `'YYYY-MM'`。
- `sales.gross_profit`（粗利金額）は**計算済みの値を列として持つ**。画面を速くするためです。
- `sales` は「日付 × 店舗 × 商品」で1行。売れなかった日の行は無くて構いません。
- 前年比を出すので、**2年分**のデータが必要です（無いと前年の欄が空欄になります）。

### 検索エンジン（C）
`lib/search_engine.py` に `search(query: str) -> pandas.DataFrame` を用意すると、画面上部の検索がそちらに切り替わります。
無ければ簡易検索（商品名・読み仮名・部門・カテゴリの部分一致）になります。
DBへは `from lib import db` の `db.q(sql, params)` が使えます（接続とキャッシュ込み）。

### AI分析コメント
`lib/ai_comment.py` に `comment(context: dict) -> str` を用意すると、
経営ダッシュボードの「今月の要点」がそちらに差し替わります。
`context` には `kpi` `by_store` `by_dept` `loss` が入っています。

---

## 6. 暫定ダミーデータについて（画面用）

`lib/sample_data.py` が作っています。発表デモで話が作れるよう、次の状況をわざと仕込んであります。

| 仕込み | 画面での見え方 |
|---|---|
| 西原店の隣に2026年4月ドラッグストアが開店 | 店舗別の前年比で西原店だけ大きく前年割れ |
| ドラッグストアは日配・グロサリーが安い | バイヤー画面の競合価格比較で価格差が大きく出る |
| 港町店の惣菜が作りすぎ | ロス率ランキングで突出（約7%） |
| 惣菜は伸び、鮮魚・グロサリーは落ちている | 部門別・商品別の前年比に差が出る |
| 部門で在庫の持ち方が違う（惣菜0.5日〜グロサリー5日） | 在庫日数・発注の目安に差が出る |

規模感：8店舗・48商品（代表商品）・2年分（2024/10〜2026/9）・年商約100億円・客単価約2,000円。

データ班のデータ（ロス分析）には、**気温が急に下がった日に、AIの推奨どおり発注した中央店だけ機会ロスが小さい**
という状況が入っています（`scripts/create_transactions.py`）。
