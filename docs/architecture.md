# アーキテクチャ設計書

`docs/requirements.md`が「何を満たすべきか」なら、これは「実際にどう構成されているか」。
`deploy.sh`が実装（Infrastructure as Code）としての正、このファイルはそれを俯瞰する
読み物として位置づける。構成を変えたら両方更新すること。

最終更新: 2026-08-02

## 1. 全体構成

```
EventBridge Scheduler (平日 UTC22時=JST朝7時)
        │
        ▼
  daily-batch Lambda ──────┐
        │                  │
        ├─ DynamoDB        ├─ SES (メール送信)
        │  watchlist       │
        │  signal_history  │
        │  app_settings(読)│
        │                  │
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
- IAMロール: `lambda-daily-batch-role`
- 環境変数: `SES_SENDER` `SES_RECIPIENT` `REGISTER_FUNCTION_URL` `REGISTER_TOKEN`
  `WEEKLY_REPORT_WEEKDAY`(既定"0") `TOP_N`(既定3) `WATCHLIST_TABLE` `SIGNAL_HISTORY_TABLE`
  `SETTINGS_TABLE`
- invokeイベントの主なフラグ: `monitoring_only` `force_weekly` `simulate_date`

### register-stock
- ソース: `mnt/user-data/outputs/aws_deploy/lambda_register/handler.py` `config.py`
- トリガー: Function URL（`AuthType: NONE`、招待トークンはアプリ層で検証）
- IAMロール: `lambda-register-stock-role`
- 環境変数: `WATCHLIST_TABLE` `APPROVED_TOKENS_TABLE` `SETTINGS_TABLE`
  `MAX_WATCHLIST_SIZE`(既定5) `DAILY_BATCH_FUNCTION`(既定"daily-batch")

## 3. DynamoDBテーブル

| テーブル | 主キー | 用途 |
|---|---|---|
| `watchlist` | `ticker`(HASH) | 監視リスト |
| `signal_history` | `ticker`(HASH) + `date`(RANGE) | 前日比較用のシグナル履歴 |
| `app_settings` | `key`(HASH) | `period_mode`（期間モード）、`nasdaq100_tickers_cache`（銘柄キャッシュ） |
| `approved_tokens` | `token`(HASH) | 招待トークン（`email`属性付き） |

## 4. IAM権限マトリクス

`deploy.sh`の`put-role-policy`定義に基づく（実際のロールをこのセッションの権限では
直接読み出せないため、`deploy.sh`をコードとしての正とし、既知のドリフトは下記に明記）。

| ロール | テーブル/リソース | 権限 |
|---|---|---|
| `lambda-daily-batch-role` | `watchlist` | GetItem, PutItem, Scan |
| | `signal_history` | GetItem, PutItem, Scan |
| | `app_settings` | GetItem, **PutItem**(※1) |
| | SES | SendEmail |
| `lambda-register-stock-role` | `watchlist` | PutItem, DeleteItem, Scan |
| | `approved_tokens` | GetItem |
| | `app_settings` | GetItem, PutItem |
| | `daily-batch`関数 | InvokeFunction |
| `scheduler-invoke-batch-role` | `daily-batch`関数 | InvokeFunction |

※1: TODO#2（NASDAQ100銘柄キャッシュ）の実装時、`daily-batch`ロールに`app_settings`への
`PutItem`権限が不足していることが本番の403エラーで判明し、`aws iam put-role-policy`で
別インラインポリシー(`app-settings-cache-write`)として追加した。**2026-08-02時点で
`deploy.sh`の`batch-policy`本体には未反映だったため、この文書作成と合わせて`deploy.sh`
側も修正済み**（新規環境をデプロイし直しても同じ権限不足が再現しないようにするため）。

## 5. 既知の制約

- このセッションで使えるAWS認証情報(`claude-deploy`)は`iam:ListRolePolicies`等の読み取り
  権限を持たないため、実際にデプロイ済みのIAMポリシー内容を直接読み出して検証すること
  はできない。上表は「`deploy.sh`に書かれているべき内容」であり、実際のロールとの
  完全な一致は`deploy.sh`を再実行するか、コンソール等の別経路での確認が必要。
- Function URLのリソースポリシー（`Principal: *`が広い件）は`TODO.md`#7を参照。
