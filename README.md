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

初回起動時にデータベース（`data/supermarket.db`）を、データ班のCSVから**自動で組み立てます**（数秒〜30秒）。
ブラウザで http://localhost:8501 が開きます。

> Windowsの注意：アプリを起動したままだと `data/*.db` を作り直せません（ファイルが使用中になります）。
> データを作り直すときは、いったんアプリを止めてください。

---

## 2. データの統合のしくみ

データベースは SQLite のファイル1つです。中に3系統のテーブルが入っています。

| 系統 | テーブル | どこから来るか |
|---|---|---|
| A 画面用（ER図のテーブル） | `stores` `departments` `products` `sales` `inventory` `waste` `stockouts` `purchases` `budgets` `customers_daily` `competitors` `competitor_prices` `knowledge` ほか | **データ班のCSVを正として組み立て、足りない部分を補完**（`lib/real_data.py`。下の「補完のしかた」） |
| B データ班 | `M_STORE` `M_PRODUCT` `M_WEATHER` `T_SALES` `T_ORDER` `T_INVENTORY` `T_WASTE_DISCOUNT` ＋ ビュー `V_STORE_LOSS_SUMMARY` `V_WEATHER_HYPOTHESIS_CHECK` `V_EVENING_DISCOUNT_CANDIDATES` | `data/csv/*.csv` を取り込み、`scripts/analysis_views.sql` でビューを作成 |
| C クローラー | `crawl_flyers` `crawl_featured_items` `crawl_events` | 「競合・地域情報」画面を開いたときに取得して保存（最新の1回ぶん） |

統合は `lib/integrate.py` が行います。**足りないテーブルだけ作る**ので、起動のたびに呼ばれても害はありません。

手元で作り直すとき（アプリは止めてから）：

```bash
python scripts/build_db.py           # DBが無ければ作る（テーブルと件数の一覧も出ます）
python scripts/build_db.py --all     # data/csv/*.csv から全部作り直す（CSVを更新したとき）
```

### 補完のしかた（画面用のテーブル）

店舗・商品は**データ班のマスタ（サンフジ5店舗）に一本化**しました。
データ班のデータだけでは画面を動かすのに足りない部分を、`lib/real_data.py` が生成して埋めています。

| | 中身 |
|---|---|
| **データ班のデータそのまま** | 店舗5・商品12の名前／売価／原価。`T_SALES` の期間（2026-09-20〜26）の売上・在庫・廃棄・欠品・発注・天気。**この期間・この12商品の数字は「ロス分析」画面と一致します**（売上 3,309,470円、廃棄 53,480円、欠品 92件で照合済み） |
| **補完した生成データ** | それ以外の期間（2024-10-01〜2026-09-30 の2年ぶん。前年比に必要）／データ班に無い部門の代表商品43品（`lib/sample_data.py` の商品表から、中身が重なる5品を除いて追加）／売場面積・開店年・読み仮名・JAN／予算・客数・仕入先・競合店と競合価格・ナレッジ |

補完するときに置いた前提：

- **部門の割り当て**：データ班の区分は 惣菜・日配・生鮮 の3つですが、画面は6部門（青果・精肉・鮮魚・惣菜・日配・グロサリー）です。
  「生鮮」の3商品は、豚バラ肉・鶏もも肉を精肉、鍋用カット野菜を青果に割り当てました（`PRODUCT_EXTRA`）。
  「ロス分析」画面は、データ班の区分（生鮮）のまま表示します。
- **売れ方**：データ班の `scripts/create_transactions.py` と同じ基準販売数（惣菜30・日配40・生鮮25）と、
  気温が前日より3℃以上下がった日の鍋関連の特需（1.45倍）を使い、曜日・季節・店舗規模・特売の変動を足しています。
  AIの提案を採用する中央店だけが特需を取り切り、他店は欠品する、という筋書きも同じです。
  真夏に鍋の特需が出ないよう、「最高気温25℃未満」という条件だけ追加しています。
- **廃棄率・欠品率**：データ班の12商品は、データ班の7日分から商品ごとに計算した率を使っています。
- **規模**：データ班の販売数（1商品1店舗で1日30個前後）に合わせたため、
  全社売上は**年 約5.3億円**（代表55商品ぶん）、客単価は約1,170円です。
  暫定ダミーデータ（年商約100億円になるよう販売数を14倍に底上げ）とは桁が違います。
- 実データの1週間は、曜日による増減がありません（データ班の生成ロジックに曜日の要素が無いため）。
  日次のグラフでは、その週だけ平らに見えます。

### まだ統合していないこと（チームで決めたいこと）

- **クローラーの特売商品と、自社商品の対応づけは未実装です。**
  バイヤー画面の「競合価格との比較」のグラフは生成した競合価格（`competitor_prices`）のままで、
  クローラーが集めた実データはその下に一覧で出しています。
- 舞台の地域が、データ班は富山市、クローラーは金沢市（イオン金沢店・スギ薬局 金沢駅西店・金沢市のイベント）で、そろっていません。
- 「ロス分析」の発注・値引・機会ロス（`T_ORDER` ほか）は、データ班の7日分だけです（2年ぶんには延ばしていません）。

---

## 3. ファイル構成

| ファイル | 中身 | 元の担当 |
|---|---|---|
| `app.py` | 入口。画面の切り替えと、統合ダッシュボード（サイドバー・検索・3つの立場） | A |
| `views/loss.py` | ロス分析の画面 | B（画面は統合時に作成） |
| `views/market.py` | 競合・地域情報の画面 | C |
| `lib/db.py` | DB接続と集計クエリ。**画面から使うデータの窓口** | A |
| `lib/charts.py` | グラフの共通設定（配色・書式） | A |
| `lib/real_data.py` | **本番DBの組み立て**（データ班のCSVを正として、足りない部分を補完） | 統合 |
| `lib/sample_data.py` | 暫定ダミーデータ生成（データ班のCSVが無いときだけ使う。補完用の商品表・季節係数の出どころでもある） | A（仮） |
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
- Cloud はしばらくアクセスが無いと停止し、再開時にDBを作り直します（同じCSVからは毎回同じ内容になります）。
  そのときクローラーの保存ぶん（`crawl_*`）は消えますが、「競合・地域情報」を開けば取り直します。
- APIキーを使う場合は Cloud の「Secrets」に設定し、`.streamlit/secrets.toml` は **GitHub に上げない**でください。

手元でOCRを使うには、別途 Tesseract 本体のインストールが必要です
（Windows: https://github.com/UB-Mannheim/tesseract/wiki のインストーラで「Japanese」を追加）。
無くてもアプリは動き、OCRのボタンを押したときに案内が出ます。

---

## 5. 各担当の差し込み口（統合前から変わっていません）

### 画面用の本番データ（B）
ふだんは `data/csv/*.csv` を更新して `python scripts/build_db.py --all` を実行すれば反映されます。
店舗や商品を増やしたときは、`lib/real_data.py` の `STORE_EXTRA` `PRODUCT_EXTRA` に1行足すと、
売場面積・読み仮名・部門などを指定できます（足さなくても既定値で動きます）。

補完を使わず、自分で作ったDBをそのまま使いたいときは、`data/supermarket.db` にSQLiteファイルを置いてください
（すでにファイルがあれば、自動の組み立ては行いません）。
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

## 6. 補完データに入れてある状況

発表デモで話が作れるよう、補完ぶん（`lib/real_data.py`）に次の状況をわざと入れてあります。
どれも生成データ上の筋書きで、データ班の7日分には手を加えていません。

| 仕込み | 画面での見え方 |
|---|---|
| サンフジ南店の隣に2026年4月ドラッグストアが開店 | 店舗別の前年比で南店だけ大きく前年割れ（約−6%） |
| ドラッグストアは日配・グロサリーが安い | バイヤー画面の競合価格比較で価格差が大きく出る |
| サンフジ港店の惣菜が作りすぎ | ロス率ランキングで突出（約6%） |
| 惣菜は伸び、鮮魚・グロサリーは落ちている | 部門別・商品別の前年比に差が出る |
| 部門で在庫の持ち方が違う（惣菜0.5日〜グロサリー5日） | 在庫日数・発注の目安に差が出る |
| 気温が急に下がった日、中央店だけ鍋関連の特需を取り切る | 他店は鍋つゆ・豆腐などの欠品が多い（データ班の筋書きを2年ぶんに延長） |

データ班のデータ（ロス分析）には、**気温が急に下がった日に、AIの推奨どおり発注した中央店だけ機会ロスが小さい**
という状況が入っています（`scripts/create_transactions.py`）。

データ班のCSVが無い環境では、`lib/sample_data.py` の暫定ダミーデータ
（8店舗・48商品・年商約100億円）で動きます。
