# AWS設定のセキュリティ改善手順（2026-08-01）

Function URL Forbidden問題の解決後に残った、AWS側のセキュリティ改善タスク。
すべてWindowsのPowerShellにそのまま貼れる形（1行ずつ実行）。

実施状況を追えるよう、上から順に実行すること。

---

## 1. 登録用Lambdaの同時実行数を制限（濫用対策）

Function URLは認証なしで全世界に公開されているため、大量アクセスされると
アカウントの同時実行枠（上限10）を食い潰し、daily-batchの実行を妨げる恐れがある。
register-stockに予約枠2を設定して影響を隔離する:

```powershell
aws lambda put-function-concurrency --function-name register-stock --reserved-concurrent-executions 2 --region us-east-1
```

## 2. daily-batch の環境変数を現在のURLに更新

`REGISTER_FUNCTION_URL` は削除済みの旧URLを指したまま（メール内の登録リンクが壊れている）。

```powershell
aws lambda update-function-configuration --function-name daily-batch --environment "Variables={SES_SENDER=mohatose4929@gmail.com,SES_RECIPIENT=mohatose4929@gmail.com,REGISTER_FUNCTION_URL=https://limtvgcrufuum3of3htggiovwa0vxxaj.lambda-url.us-east-1.on.aws/}" --region us-east-1
```

反映確認:

```powershell
aws lambda get-function-configuration --function-name daily-batch --region us-east-1 --query "Environment.Variables.REGISTER_FUNCTION_URL"
```

## 3. daily-batch / register-stock の再デプロイ

- daily-batch: signals.pyの修正（デッドコード削除）を含むZIPで更新
- register-stock: ティッカー検証の修正（全角文字の拒否）を含むZIPで更新

リポジトリ直下で（Git Bashなら `bash deploy.sh` でも可。PowerShellの場合）:

```powershell
Compress-Archive -Force -Path handler.py,signals.py,data_sources.py,ranking.py,report.py -DestinationPath daily_batch_new.zip
aws lambda update-function-code --function-name daily-batch --zip-file fileb://daily_batch_new.zip --region us-east-1
```

```powershell
aws lambda update-function-code --function-name register-stock --zip-file fileb://register.zip --region us-east-1
```

※ `register.zip` はリポジトリ内の修正済みのものを使用（handler.py単体入り）。

## 4. （推奨・任意）リソースポリシーの厳密化

現在の `public-url-invoke-function` 文は無条件の `Principal: *` で、
Function URL経由に限定されていない。CLIでは条件を付けられないため、
厳密化するにはコンソールでURLを作り直す:

1. AWSコンソール → Lambda → `register-stock` → 「設定」→「関数URL」→ 削除
2. 手動で付けた2つの許可文を削除:
   ```powershell
   aws lambda remove-permission --function-name register-stock --statement-id public-url-access --region us-east-1
   aws lambda remove-permission --function-name register-stock --statement-id public-url-invoke-function --region us-east-1
   ```
3. コンソールから「関数URLを作成」（認証タイプ: NONE）
   → AWSが適切な条件付きポリシーを自動生成する
4. **URLが変わる**ので、手順2のコマンドを新URLで再実行して環境変数を更新
5. ブラウザで新URLを開いて動作確認

※ この手順を実施しない場合でも即座に危険ではない（できるのは任意のAWSアカウント
からの直接Invoke＝watchlistへの登録のみ）が、実施を推奨。

## 5. 調査用IAMユーザーの削除（必須）

調査に使った `diag-readonly-temp` のアクセスキーはチャットに貼られたため
漏洩済みとして扱い、ユーザーごと削除する。キーは2つ作られている点に注意:

```powershell
aws iam delete-access-key --user-name diag-readonly-temp --access-key-id AKIASBUG6P4U6APW3I4S
aws iam delete-access-key --user-name diag-readonly-temp --access-key-id AKIASBUG6P4U2V6TQFV4
aws iam detach-user-policy --user-name diag-readonly-temp --policy-arn "arn:aws:iam::aws:policy/AWSLambda_ReadOnlyAccess"
aws iam detach-user-policy --user-name diag-readonly-temp --policy-arn "arn:aws:iam::aws:policy/CloudWatchLogsReadOnlyAccess"
aws iam delete-user --user-name diag-readonly-temp
```

削除確認（エラー NoSuchEntity が出れば成功）:

```powershell
aws iam get-user --user-name diag-readonly-temp
```

※ ターミナルのマスキング機能でアクセスキーIDが `••••` になって
コマンドが失敗する場合は、キーIDを手入力すること。

## 6. E2Eテスト

1. ブラウザで登録URLを開く → フォーム表示
2. `NVDA` などを登録 → 「登録しました」表示
3. DynamoDB確認:
   ```powershell
   aws dynamodb scan --table-name watchlist --region us-east-1
   ```
4. daily-batch手動実行:
   ```powershell
   aws lambda invoke --function-name daily-batch --region us-east-1 out.json
   type out.json
   ```
5. メール受信を確認（監視レポート）
6. メール内の登録リンクが新URLを指していることを確認

---

## 実施済み（記録）

- [x] Function URL Forbidden解消: `lambda:InvokeFunction` 許可を追加（2026-08-01）
- [x] deploy.sh修正: 両許可の付与・実パスへの追従・schedulerロール追加・エラー握り潰し解消
- [x] register-stock handler: ティッカー検証をASCII限定の正規表現に修正
- [x] register.zip再生成（修正版handler入り）
