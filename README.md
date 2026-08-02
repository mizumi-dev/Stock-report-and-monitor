# 株式レポートサービス デプロイ手順

## サービスURL（銘柄登録・削除・期間モード変更・仕組みの説明）

```
https://limtvgcrufuum3of3htggiovwa0vxxaj.lambda-url.us-east-1.on.aws/?token=<招待トークン>
```

招待トークンは`approved_tokens`テーブルに保存されている（パスワード相当、他人に
共有しないこと）。忘れた場合の再確認:
```bash
aws dynamodb scan --table-name approved_tokens --region us-east-1
```

事前検証済み: シグナル計算・ランキング・レポート生成・Lambda Layerビルドは
このドキュメント作成時に実データで動作確認済みです。
AWS CLIコマンド自体は（AWSアカウントへの実行権限がないため）未実行です。

## 前提
- AWS CLIがインストール・設定済み（`aws configure`）
- IAMユーザーにLambda/DynamoDB/SES/IAM/EventBridge Scheduler/SNS/CloudWatchへの権限があること

## 手順

### 1. Lambda Layerをビルド
```bash
cd layer
bash build_layer.sh
```
→ `layer.zip` (約43MB) が生成されます。50MB以下なので直接アップロード可能です。

### 2. deploy.shの変数を書き換え
`deploy.sh` の冒頭にある以下を、自分の情報に書き換えてください。
```bash
export SES_SENDER="your-email@gmail.com"
export SES_RECIPIENT="your-email@gmail.com"
```

### 3. 一括デプロイ実行
```bash
bash deploy.sh
```
DynamoDB・IAMロール・Lambda・Function URL・EventBridge Schedulerが自動作成されます。

### 4. 手動で必要な作業（自動化できない部分）
- 実行後、`SES_SENDER`宛に確認メールが届くので**リンクをクリック**してSES検証を完了してください
- SESはデフォルトで「サンドボックスモード」のため、**送信先(`SES_RECIPIENT`)も同様に検証**が必要です
  ```bash
  aws ses verify-email-identity --email-address your-recipient@gmail.com --region us-east-1
  ```

### 5. 動作確認
```bash
aws lambda invoke --function-name daily-batch --region us-east-1 /tmp/out.json
cat /tmp/out.json
```
成功すればメールが届きます。

## 既知の注意点
- Nasdaq APIは非公式内部APIのため、将来的に仕様変更で壊れる可能性があります
- 週次レポート（銘柄選定）は月曜日に送信される設定です（`WEEKLY_REPORT_WEEKDAY`環境変数で変更可）
- 日次レポート（監視）は登録銘柄が0件の場合はスキップされます
- cronは `cron(0 22 ? * MON-FRI *)`（UTC 22時=米東部17〜18時、サマータイム非対応の固定値）
