# AWS設定の改善手順（2026-08-01 更新）

Function URL Forbidden問題の解決後に残ったAWS側の作業。
すべてWindowsのPowerShellにそのまま貼れる形（1行ずつ実行）。

上から順に実行すること（特に2〜4は、5でコードを再デプロイする前に済ませる必要がある。
先にコードだけ入れ替えると、テーブルもIAM権限もない状態でエラーになる）。

---

## 1. 登録用Lambdaの同時実行数を制限（濫用対策）

Function URLは認証なしで全世界に公開されているため、大量アクセスされると
アカウントの同時実行枠（上限10）を食い潰し、daily-batchの実行を妨げる恐れがある。
register-stockに予約枠2を設定して影響を隔離する:

```powershell
aws lambda put-function-concurrency --function-name register-stock --reserved-concurrent-executions 2 --region us-east-1
```

## 2. 承認済みトークン用テーブルの作成

「登録は承認済みメールアドレスのみ」を実現するため、事前共有トークン方式を採用。
`?token=xxx` を持つ人だけが登録・削除できる。

```powershell
aws dynamodb create-table --table-name approved_tokens --attribute-definitions AttributeName=token,AttributeType=S --key-schema AttributeName=token,KeyType=HASH --billing-mode PAY_PER_REQUEST --region us-east-1
```

## 3. 自分用の招待トークンを発行

ランダムなトークンを生成し、自分のメールアドレスに紐づけて登録する
（`python`コマンドが使えない場合は適当な32文字ランダム文字列で代用可）:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

出力された文字列を控えて（例: `AbC123...`）、以下の `<TOKEN>` を置き換えて実行:

```powershell
aws dynamodb put-item --table-name approved_tokens --item "{\"token\":{\"S\":\"<TOKEN>\"},\"email\":{\"S\":\"mohatose4929@gmail.com\"}}" --region us-east-1
```

**このトークンは今後、登録用URLに `?token=<TOKEN>` として毎回付けて使う。
パスワード相当なので他人に共有しないこと。**

## 4. register-stock の IAM権限を更新

新しいhandler.pyは以下を追加で必要とする: `watchlist`への削除(DeleteItem)・一覧取得(Scan)、
`approved_tokens`への参照(GetItem)。現在のロールにはこれらが無いため追加する:

```powershell
aws iam put-role-policy --role-name lambda-register-stock-role --policy-name register-policy --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"dynamodb:PutItem\",\"dynamodb:DeleteItem\",\"dynamodb:Scan\"],\"Resource\":\"arn:aws:dynamodb:us-east-1:140943195945:table/watchlist\"},{\"Effect\":\"Allow\",\"Action\":[\"dynamodb:GetItem\"],\"Resource\":\"arn:aws:dynamodb:us-east-1:140943195945:table/approved_tokens\"},{\"Effect\":\"Allow\",\"Action\":[\"logs:CreateLogGroup\",\"logs:CreateLogStream\",\"logs:PutLogEvents\"],\"Resource\":\"*\"}]}"
```

（`put-role-policy` は同名なら上書きしてくれるので、既存分を消す必要はない）

## 5. register-stock / daily-batch の再デプロイ

このリポジトリの `Second` ブランチ（あなたがpush済み）に最新版が入っている。
そのフォルダで:

```powershell
aws lambda update-function-code --function-name register-stock --zip-file fileb://register.zip --region us-east-1
```

```powershell
Compress-Archive -Force -Path handler.py,signals.py,data_sources.py,ranking.py,report.py -DestinationPath daily_batch_new.zip
aws lambda update-function-code --function-name daily-batch --zip-file fileb://daily_batch_new.zip --region us-east-1
```

## 6. 環境変数の更新（register-stock / daily-batch 両方）

**register-stock** — 監視リストの上限件数（5件）:

```powershell
aws lambda update-function-configuration --function-name register-stock --environment "Variables={MAX_WATCHLIST_SIZE=5}" --region us-east-1
```

**daily-batch** — 現在のURL・招待トークン・上位件数。`<TOKEN>` は手順3で発行したもの:

```powershell
aws lambda update-function-configuration --function-name daily-batch --environment "Variables={SES_SENDER=mohatose4929@gmail.com,SES_RECIPIENT=mohatose4929@gmail.com,REGISTER_FUNCTION_URL=https://limtvgcrufuum3of3htggiovwa0vxxaj.lambda-url.us-east-1.on.aws/,REGISTER_TOKEN=<TOKEN>,TOP_N=3}" --region us-east-1
```

反映確認:

```powershell
aws lambda get-function-configuration --function-name daily-batch --region us-east-1 --query "Environment.Variables"
```

## 7. （推奨・任意）リソースポリシーの厳密化

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
4. **URLが変わる**ので、手順6のコマンドを新URLで再実行して環境変数を更新
5. ブラウザで `新URL?token=<TOKEN>` を開いて動作確認

※ 実施しなくても即座に危険ではない（トークンを知らない限り登録も削除もできない）が、実施を推奨。

## 8. 調査用IAMユーザーの削除（必須）

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

※ ターミナルのマスキング機能でアクセスキーIDが `••••` になってコマンドが失敗する場合は、キーIDを手入力すること。

## 9. E2Eテスト

1. ブラウザで `登録URL?token=<TOKEN>` を開く → フォームと現在の監視リスト（空のはず）が表示
2. `NVDA` などを登録 → 「登録しました」表示、フォームに戻ると一覧に追加され`[削除]`リンクが出る
3. 5件登録した状態で6件目を登録 → 「上限5件です」エラーが出ることを確認
4. `[削除]` リンクを押して1件削除 → 一覧から消える
5. `token`無し、または適当な`token`でアクセス → 「アクセス権がありません」(403)になることを確認
6. DynamoDB確認:
   ```powershell
   aws dynamodb scan --table-name watchlist --region us-east-1
   ```
7. daily-batch手動実行（テスト用に選別レポートも強制発火できる）:
   ```powershell
   aws lambda invoke --function-name daily-batch --region us-east-1 --payload "{\"force_weekly\":true}" --cli-binary-format raw-in-base64-out out.json
   type out.json
   ```
8. メール受信を確認（選別レポート・監視レポート両方）。選別レポート内の登録リンクに `?token=<TOKEN>` が付いていることを確認

## 10. daily-batchにレポート即時送信ボタンを追加

「選定レポート」「監視レポート」をスケジュールを待たずにボタンクリックで送れるようにした。
register-stockと同じ招待トークンで認証する。

**IAM権限の追加**（daily-batchのロールに`approved_tokens`参照権限がまだ無いため）:

```powershell
aws iam put-role-policy --role-name stock-report-batch-role --policy-name batch-policy --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"dynamodb:GetItem\",\"dynamodb:PutItem\",\"dynamodb:Scan\"],\"Resource\":[\"arn:aws:dynamodb:us-east-1:140943195945:table/watchlist\",\"arn:aws:dynamodb:us-east-1:140943195945:table/signal_history\"]},{\"Effect\":\"Allow\",\"Action\":[\"dynamodb:GetItem\"],\"Resource\":\"arn:aws:dynamodb:us-east-1:140943195945:table/approved_tokens\"},{\"Effect\":\"Allow\",\"Action\":[\"ses:SendEmail\"],\"Resource\":\"*\"},{\"Effect\":\"Allow\",\"Action\":[\"logs:CreateLogGroup\",\"logs:CreateLogStream\",\"logs:PutLogEvents\"],\"Resource\":\"*\"}]}"
```

（実際のロール名は`lambda-daily-batch-role`ではなく`stock-report-batch-role`。手順4で判明した実名を使用）

**コード再デプロイ**（handler.pyのみ変更、他ファイルは前回と同じ）:

```powershell
Compress-Archive -Force -Path handler.py,signals.py,data_sources.py,ranking.py,report.py -DestinationPath daily_batch_new.zip
aws lambda update-function-code --function-name daily-batch --zip-file fileb://daily_batch_new.zip --region us-east-1
```

**daily-batchにFunction URLを発行**（register-stockと同じ手順。2025年10月仕様なので2つの許可が両方必要）:

```powershell
aws lambda create-function-url-config --function-name daily-batch --auth-type NONE --region us-east-1
aws lambda add-permission --function-name daily-batch --action lambda:InvokeFunctionUrl --principal "*" --function-url-auth-type NONE --statement-id public-url-access --region us-east-1
aws lambda add-permission --function-name daily-batch --action lambda:InvokeFunction --principal "*" --statement-id public-url-invoke-function --region us-east-1
aws lambda get-function-url-config --function-name daily-batch --region us-east-1 --query FunctionUrl --output text
```

出てきたURLの末尾に `?token=<招待トークン>` を付けたものが、レポート送信ボタン付きページのURL。

---

## 実施済み（記録）

- [x] Function URL Forbidden解消: `lambda:InvokeFunction` 許可を追加（2026-08-01）
- [x] deploy.sh修正: 両許可の付与・実パスへの追従・schedulerロール追加・エラー握り潰し解消
- [x] register-stock handler: ティッカー検証をASCII限定の正規表現に修正
- [x] register-stock handler: 招待トークン認証・削除機能・監視リスト上限5件を追加
- [x] daily-batch handler/report: 招待トークン付きURLの組み立て、上位件数(TOP_N)の環境変数化、
      `force_weekly`によるテスト用強制実行に対応
- [x] register.zip再生成（修正版handler入り）
- [x] AWS側の反映（本ファイルの手順2〜6）
- [x] daily-batch/report: レポート即時送信ボタン(選定/監視)をFunction URLで追加
- [ ] daily-batchのFunction URL発行・IAM権限追加（本ファイルの手順10、要実施）
