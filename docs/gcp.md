# GCP でデータ統合基盤を動かす手順

GCP の設定が無いときは、今まで通り手元の SQLite と `data/raw` で動きます。
この手順を終えると、次の形に切り替わります。

```
Cloud Scheduler（毎朝6時）
  → Cloud Run ジョブ sunfuji-collect（scripts/collect.py。Dockerfile のコンテナ）
  → Cloud Storage  gs://<バケット>/raw/<種類>/<日付>.csv      … 生データ（正）
  → BigQuery 外部テーブル raw_<種類>（上のCSVをそのまま読む）
  → BigQuery ビュー crawl_* / crawl_*_latest / _ingest_log    … 履歴・画面用
  → Streamlit Cloud の画面（「競合・地域情報」「データ基盤」）

データ班のデータ（data/csv の M_* / T_*）→ BigQuery のテーブル ＋ 分析ビュー V_* → 「ロス分析」
```

統合ダッシュボードの画面用テーブル（生成した2年分の売上など）は、これまで通りアプリ内の SQLite です。

| 使うもの | 役割 | 料金の目安（この規模） |
|---|---|---|
| Cloud Storage | 生データの置き場所 | 数MBなので、ほぼ0円（東京リージョンは無料枠の対象外ですが、1GBで月4円ほど） |
| BigQuery | 履歴・分析（テーブルとビュー） | 無料枠（保存10GB・クエリ1TB/月）に収まる |
| Cloud Run ジョブ | 毎朝の収集 | 無料枠に収まる（1回1〜2分） |
| Cloud Scheduler | 毎朝の起動 | 3ジョブまで無料 |
| Cloud Build / Artifact Registry | コンテナを作って置く | 無料枠に収まる（デプロイするときだけ使う） |

---

## 0. 最初に（GCPのコンソールで、ブラウザから）

1. https://console.cloud.google.com で**プロジェクトを作る**（例：`sunfuji-dashboard`）。プロジェクトID を控える
2. 「お支払い」で**請求先アカウントを作って、プロジェクトにつなぐ**（クレジットカードの登録が必要。無料トライアルのクレジットも使えます）
3. 「お支払い」→「予算とアラート」で**予算アラートを作る**（例：月1,000円、50%・90%・100%で通知）。使いすぎに気づけるようにするため
4. 画面右上の **Cloud Shell**（`>_` のアイコン）を開く。以降のコマンドはすべて Cloud Shell で実行します

## 1. 変数を決める

```bash
PROJECT_ID=sunfuji-dashboard          # ← 自分のプロジェクトID
REGION=asia-northeast1                # 東京
BUCKET=${PROJECT_ID}-sunfuji-raw      # 生データのバケット名（世界で一意である必要がある）
COLLECTOR=sunfuji-collector@${PROJECT_ID}.iam.gserviceaccount.com
DASHBOARD=sunfuji-dashboard@${PROJECT_ID}.iam.gserviceaccount.com
gcloud config set project $PROJECT_ID
```

> Cloud Shell を開き直したときは、この変数をもう一度設定してください。

## 2. API を有効にする

```bash
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com \
  bigquery.googleapis.com storage.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com
```

## 3. バケットとサービスアカウントを作る

サービスアカウントは2つ。**収集用（書く）**と**画面用（読むだけ）**に分けます。

```bash
gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access

gcloud iam service-accounts create sunfuji-collector --display-name="sunfuji 毎朝の自動収集"
gcloud iam service-accounts create sunfuji-dashboard --display-name="sunfuji 画面（読み取り専用）"

# 収集用：バケットに書ける
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member=serviceAccount:$COLLECTOR --role=roles/storage.objectUser

# 画面用：バケットを読める・BigQuery を読める・クエリを実行できる
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member=serviceAccount:$DASHBOARD --role=roles/storage.objectViewer
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member=serviceAccount:$DASHBOARD --role=roles/bigquery.dataViewer
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member=serviceAccount:$DASHBOARD --role=roles/bigquery.jobUser
```

## 4. リポジトリを取ってきて、BigQuery の中身を作る

```bash
git clone https://github.com/fusage/Local-supermarket.git
cd Local-supermarket
git checkout main                     # ← GCP対応を入れたブランチ
pip install --user -r requirements.txt
python3 scripts/gcp_setup.py --project $PROJECT_ID --bucket $BUCKET
```

データセット `sunfuji` に、外部テーブル `raw_*`、ビュー `crawl_*` `crawl_*_latest` `_ingest_log`、
データ班のテーブル `M_*` `T_*` と分析ビュー `V_*` ができます（BigQuery のコンソールで確認できます）。

> 「認証情報が見つからない」と出たら `gcloud auth application-default login` を実行してから、もう一度。
> データ班のCSVを更新したときは `python3 scripts/gcp_setup.py --project $PROJECT_ID --bucket $BUCKET --csv-only` で入れ直せます。

## 5. 収集ジョブを Cloud Run にデプロイして、1回動かす

```bash
gcloud run jobs deploy sunfuji-collect --source . --region $REGION \
  --service-account $COLLECTOR \
  --set-env-vars GCP_PROJECT=$PROJECT_ID,GCP_BUCKET=$BUCKET \
  --task-timeout 15m --max-retries 1

gcloud run jobs execute sunfuji-collect --region $REGION --wait
gcloud storage ls -r gs://$BUCKET/raw/
```

- 初回は「Artifact Registry のリポジトリを作るか」と聞かれるので `Y`
- `raw/weather/2026-10-01.csv` のようなファイルが並べば成功です。BigQuery で確かめるなら：

```bash
bq query --use_legacy_sql=false "SELECT * FROM \`$PROJECT_ID.sunfuji._ingest_log\` ORDER BY path DESC"
```

> デプロイで権限エラー（`PERMISSION_DENIED` … build）が出たら、ビルドに使うアカウントに権限を足してから再実行：
> ```bash
> PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
> gcloud projects add-iam-policy-binding $PROJECT_ID \
>   --member=serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com --role=roles/run.builder
> ```

## 6. 毎朝6時に動くようにする

```bash
gcloud run jobs add-iam-policy-binding sunfuji-collect --region $REGION \
  --member=serviceAccount:$COLLECTOR --role=roles/run.invoker

gcloud scheduler jobs create http sunfuji-collect-daily --location $REGION \
  --schedule "0 6 * * *" --time-zone "Asia/Tokyo" \
  --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/sunfuji-collect:run" \
  --http-method POST --oauth-service-account-email $COLLECTOR
```

すぐ試すなら `gcloud scheduler jobs run sunfuji-collect-daily --location $REGION`。
実行の記録は、コンソールの「Cloud Run」→「ジョブ」→ `sunfuji-collect` で見られます。

## 7. Streamlit Cloud の画面を BigQuery につなぐ

画面用サービスアカウントの鍵を作り、Streamlit Cloud の **Secrets** に貼ります。

```bash
gcloud iam service-accounts keys create key.json --iam-account=$DASHBOARD
BUCKET=$BUCKET python3 - <<'EOF'
import json, os
k = json.load(open("key.json"))
print('[gcp]')
print(f'project = "{k["project_id"]}"')
print(f'bucket = "{os.environ.get("BUCKET", "")}"')
print('dataset = "sunfuji"')
print()
print('[gcp_service_account]')
for a, b in k.items():
    print(f'{a} = {json.dumps(b)}')
EOF
```

1. 表示された内容をまるごとコピーする（`BUCKET` が空なら、手順1の変数を設定し直す）
2. https://share.streamlit.io → アプリの「⋮」→「Settings」→「Secrets」に貼って保存
3. **Cloud Shell の鍵ファイルを消す**：`rm key.json`

アプリが再起動したら「🗄️ データ基盤」画面を開き、
「GCP で動作中：BigQuery `<プロジェクト>.sunfuji`」と出ていれば完了です。

> - 鍵は**絶対に GitHub に上げない**でください（`.streamlit/secrets.toml` は `.gitignore` 済み）。
>   漏れたら、コンソールの「IAM」→「サービスアカウント」→ `sunfuji-dashboard` →「鍵」で削除して作り直します。
> - 手元のPCで GCP 版を試すときは、`.streamlit/secrets.toml` に同じ内容を書くか、
>   `gcloud auth application-default login` をしたうえで環境変数 `GCP_PROJECT` `GCP_BUCKET` を設定します。

---

## うまくいかないとき

| 症状 | 見るところ |
|---|---|
| 「データ基盤」に「GCP で動作中」と出ない | Secrets の `[gcp]` に `project` と `bucket` があるか |
| 出るが、表が空 | 手順5のジョブが成功しているか（`gcloud storage ls -r gs://$BUCKET/raw/`）。画面は5分キャッシュするので少し待つ |
| 「ロス分析」だけ古い・出ない | `scripts/gcp_setup.py` を実行したか。BigQuery に届かないときは手元の SQLite に切り替えて表示します |
| 毎朝増えない | コンソールの「Cloud Scheduler」で実行結果、「Cloud Run」→「ジョブ」でログ |

## GCP をやめるとき

Streamlit の Secrets から `[gcp]` を消せば、手元の SQLite 版に戻ります。
課金を止めるには、コンソールでプロジェクトごと削除するのが確実です。
