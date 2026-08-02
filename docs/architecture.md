# アーキテクチャ設計書

`docs/requirements.md`が「何を満たすべきか」なら、これは「実際にどう構成されているか」。
`deploy.sh`が実装（Infrastructure as Code）としての正、このファイルはそれを俯瞰する
読み物として位置づける。構成を変えたら両方更新すること。

最終更新: 2026-08-02（マルチテナント化対応）

## 0. マルチテナント設計の方針

外部公開（誰でも申込み可能）に向けて、以下の方針でデータを利用者ごとに分離した
（`expert-panel-review`での検討結果）:

- **利用者の識別は招待トークンそのものを使う。別途user_idは発行しない**
  （トークン漏洩時の影響範囲は元々認証もデータも一体なので新たなリスク増加はない）
- **利用者ごとに分離するもの**: watchlist（監視リスト）、期間モード、レポート送信先、
  レポート内リンクのトークン
- **共有のまま（利用者ごとに分けない）もの**: 選定基準(PEG/成長率/TOP_N)、
  NASDAQ100構成銘柄キャッシュ、シグナル計算結果（ただし「モードごと」には分離。
  同じ期間モードを使う利用者同士は計算結果を使い回す）
- **承認方式**: 管理者承認ではなく、メールアドレス確認（ダブルオプトイン）による
  完全自動のセルフサーブ。運用者(あなた)の手作業は発生しない

## 1. 全体構成

```
EventBridge Scheduler (平日 UTC22時=JST朝7時)
        │
        ▼
  daily-batch Lambda ──────┬─ SES (メール送信、正常系レポート・TODO#1のエラー通知)
        │                  │
        ├─ DynamoDB        └─ CloudWatch Alarm(Errors>=1) → SNS → メール
        │  watchlist          (TODO#5。SESでの直接通知が機能しないケースの二重の安全網)
        │  signal_history
        │  app_settings(読)
        │
        └─ 外部API: Nasdaq(銘柄一覧) / yfinance(株価)

register-stock Lambda (Function URL, 認証なしURL+招待トークン)
        │
        ├─ DynamoDB
        │  watchlist(読み書き)
        │  approved_tokens(読)
        │  app_settings(読み書き)
        │
        └─ daily-batch Lambdaを非同期invoke(即時送信ボタン用)
```

## 2. Lambda関数

### daily-batch
- ソース: `handler.py` `config.py` `signals.py` `data_sources.py` `ranking.py` `report.py`
- トリガー: EventBridge Scheduler（定期実行）、または`register-stock`からの非同期invoke
- IAMロール: `stock-report-batch-role`（2026-08-02、`deploy.sh`側の記載を実際の
  ロール名に合わせて修正済み。旧ドリフトの経緯は下記5.参照）
- 環境変数: `SES_SENDER` `SES_RECIPIENT`(エラー通知の送付先=`ADMIN_EMAIL`として流用)
  `REGISTER_FUNCTION_URL` `WEEKLY_REPORT_WEEKDAY`(既定"0") `TOP_N`(既定3)
  `WATCHLIST_TABLE` `SIGNAL_HISTORY_TABLE` `SETTINGS_TABLE` `APPROVED_TOKENS_TABLE`
- invokeイベントの主なフラグ: `monitoring_only` `force_weekly` `simulate_date`
  `owner_token`(指定時はその利用者の分だけ送る。register-stockの即時送信ボタン用)
  `_test_trigger_failure`
- 定期実行時は`approved_tokens`のstatus=approved全員を対象に、期間モードごとに
  グループ化して選定レポートをスキャン、利用者ごとに個別のメール・個別のトークン
  入りリンクで送信する(`run_selection_reports`/`run_monitoring_reports`)

### register-stock
- ソース: `mnt/user-data/outputs/aws_deploy/lambda_register/handler.py` `config.py`
- トリガー: Function URL（`AuthType: NONE`、招待トークンはアプリ層で検証）
- IAMロール: `stock-report-register-role`（2026-08-02、`deploy.sh`側の記載を実際の
  ロール名に合わせて修正済み。旧ドリフトの経緯は下記5.参照）
- 環境変数: `WATCHLIST_TABLE` `APPROVED_TOKENS_TABLE` `SETTINGS_TABLE`
  `MAX_WATCHLIST_SIZE`(既定5) `DAILY_BATCH_FUNCTION`(既定"daily-batch")
  `SES_SENDER`(申込み確認メールの送信元) `REGISTER_FUNCTION_URL`(確認メールの
  リンク生成用、自己参照)
- ダブルオプトイン: `?signup_email=...`で申込み受付(簡易CAPTCHA付き)→確認メール送信→
  `?token=...&confirm=1`のリンククリックでapproved化

## 3. DynamoDBテーブル

| テーブル | 主キー | 用途 |
|---|---|---|
| `watchlist` | `owner_token`(HASH) + `ticker`(RANGE) | 利用者ごとの監視リスト（複合キーで分離、同じtickerを別利用者が独立登録可能） |
| `signal_history` | `ticker`(HASH) + `date`(RANGE、値は`"YYYY-MM-DD#MODE"`) | 前日比較用のシグナル履歴。モードごとに共有（利用者ごとではない） |
| `app_settings` | `key`(HASH) | `"{token}#period_mode"`＝利用者ごとの期間モード、`nasdaq100_tickers_cache`＝共有の銘柄キャッシュ |
| `approved_tokens` | `token`(HASH) | 利用者そのもの。`email`・`status`(`pending`/`approved`)・`requested_at`/`approved_at`属性 |

## 3a. 監視・通知リソース（TODO#5）

| リソース | 内容 |
|---|---|
| CloudWatch Alarm `daily-batch-errors` | `AWS/Lambda`名前空間の`Errors`メトリクス(FunctionName=daily-batch)がSum>=1(5分間隔)でALARM |
| SNSトピック `daily-batch-alarm-topic` | Alarmのアクション先。`SES_RECIPIENT`のメールアドレスをemailプロトコルで購読（購読確認メールのリンククリックが必要） |

TODO#1で実装したSES直接通知（`_notify_failure`）と役割が重なるように見えるが、
**Lambda自体が起動できない**（コード全体が壊れて`handler()`にすら到達しない等）場合は
SES直接通知も実行されないため、CloudWatch Alarmは独立した経路からの二重の安全網として
機能する。実地確認: `_test_trigger_failure`イベントで意図的に例外を発生させ、
CloudWatch Alarmが`INSUFFICIENT_DATA`→`ALARM`に遷移しSNS経由でメールが届くことを確認済み。

## 4. IAM権限マトリクス

`deploy.sh`の`put-role-policy`定義に基づく（実際のロールをこのセッションの権限では
直接読み出せないため、`deploy.sh`をコードとしての正とし、既知のドリフトは下記に明記）。

| ロール | テーブル/リソース | 権限 |
|---|---|---|
| `stock-report-batch-role`(daily-batch) | `watchlist` | GetItem, PutItem, Scan, **Query**(※2) |
| | `signal_history` | GetItem, PutItem, Scan, Query |
| | `app_settings` | GetItem, **PutItem**(※1) |
| | `approved_tokens` | **GetItem, Scan**(※2) |
| | SES | SendEmail |
| `stock-report-register-role`(register-stock) | `watchlist` | PutItem, DeleteItem, Scan, **Query**(※2) |
| | `approved_tokens` | GetItem, PutItem, Scan, **DeleteItem**(※3) |
| | `app_settings` | GetItem, PutItem, **DeleteItem**(※3) |
| | `daily-batch`関数 | InvokeFunction |
| | SES | **SendEmail**(※2、申込み確認メール用) |
| `scheduler-invoke-batch-role` | `daily-batch`関数 | InvokeFunction |
| `claude-deploy`(デプロイ用IAMユーザー、Lambdaロールではない) | SNSトピック`daily-batch-alarm-topic` | CreateTopic, Subscribe, SetTopicAttributes, GetTopicAttributes, ListSubscriptionsByTopic |
| | CloudWatch Alarm`daily-batch-errors` | PutMetricAlarm, DescribeAlarms, DeleteAlarms |
| | `watchlist`/`approved_tokens`/`app_settings`/`signal_history` | DeleteTable, CreateTable, UpdateItem, Query(マルチテナント化のテーブル移行作業用) |

※2: マルチテナント化(2026-08-02)で追加。`deploy.sh`にも反映済み。

※3: 自己退会機能(2026-08-02)追加時に権限付与が漏れており、実際に退会操作を行った
利用者で本番エラー(500)が発生して発覚。`watchlist`にはDeleteItemがあったため
監視リストの削除だけ成功し、`app_settings`の削除でエラー→処理中断となり、
`approved_tokens`の削除まで到達せずアクセス用トークンが無効化されない状態になった
（データはこの状態で壊れてはいないが、退会自体は未完了だった）。
`iam:PutRolePolicy`を`stock-report-register-role`に限定して追加発行してもらい、
本番ロールに直接反映して解消。`deploy.sh`にも反映済み。

※1: TODO#2（NASDAQ100銘柄キャッシュ）の実装時、`daily-batch`ロールに`app_settings`への
`PutItem`権限が不足していることが本番の403エラーで判明し、`aws iam put-role-policy`で
別インラインポリシー(`app-settings-cache-write`)として追加した。**2026-08-02時点で
`deploy.sh`の`batch-policy`本体には未反映だったため、この文書作成と合わせて`deploy.sh`
側も修正済み**（新規環境をデプロイし直しても同じ権限不足が再現しないようにするため）。

## 5. 既知の制約

- このセッションで使えるAWS認証情報(`claude-deploy`)は`iam:ListRolePolicies`等の読み取り
  権限を持たないため、実際にデプロイ済みのIAMポリシー内容を直接読み出して検証すること
  はできない。上表は「`deploy.sh`に書かれているべき内容」であり、実際のロールとの
  完全な一致は`deploy.sh`を再実行するか、コンソール等の別経路での確認が必要
  （ロール名(`Role`属性)自体は`lambda:GetFunctionConfiguration`権限があれば別途確認可能で、
  2026-08-02にこの権限で確認しIAMロール名のドリフトを修正した。ポリシー内容までは未確認）。
- Function URLのリソースポリシー（`Principal: *`が広い件）は`TODO.md`#7を参照。
- **IAMロール名のドリフト — 対応済み（2026-08-02）**: `deploy.sh`は当初
  `lambda-daily-batch-role`/`lambda-register-stock-role`という名前を使っていたが、
  実際にデプロイされているロールは`stock-report-batch-role`/
  `stock-report-register-role`という別名だった（マルチテナント化のIAM権限追加作業で発覚、
  `aws lambda get-function-configuration --query Role`で確認）。恐らく初回デプロイ時に
  手動で名前を変えたか、`deploy.sh`とは別の手順で作られたと見られる。実害はなかった
  （正しいロールに権限を追加できていた）が、`deploy.sh`をそのまま再実行した場合に
  旧ロール名で新規ロールが作られ、実際に使われているロールに権限が反映されないリスクが
  あったため、`deploy.sh`側のロール名を実際の名前(`stock-report-batch-role`/
  `stock-report-register-role`)に合わせて修正した。
