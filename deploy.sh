#!/bin/bash
# ===========================================================
# 株式レポートサービス AWS一括構築スクリプト
# 前提: aws configure 済み（AWS CLIに認証情報が設定済み）
# 使い方: 下記の変数を書き換えてから bash deploy.sh を実行
#
# 注意:
# - 2025年10月以降に作成するFunction URLは、リソースポリシーに
#   lambda:InvokeFunctionUrl と lambda:InvokeFunction の両方が必要
#   （片方だけだと403 Forbiddenになる。本スクリプトは両方を付与する）
# - Windows (Git Bash) では zip コマンドが無い場合がある。その場合は
#   PowerShell の Compress-Archive で同名のzipを作ってから実行する
# ===========================================================
set -euo pipefail

# ---- 事前に書き換えてください ----
export AWS_REGION="us-east-1"
export SES_SENDER="noreply@pegsignal.com"       # pegsignal.comをSESでドメイン認証済み(TXT+DKIM)
export SES_RECIPIENT="amsllr759@gmail.com"      # 事業用アドレス。レポート送付先(サンドボックスなら要検証)
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
# -----------------------------------

# リポジトリのレイアウト（このスクリプトからの相対パス）
DAILY_BATCH_FILES="handler.py config.py signals.py data_sources.py ranking.py report.py"
REGISTER_DIR="mnt/user-data/outputs/aws_deploy/lambda_register"
LAYER_ZIP="layer.zip"

echo "=== 1. DynamoDBテーブル作成 ==="
# owner_token(HASH)+ticker(RANGE)の複合キー。利用者ごとにwatchlistを分離するため
# (同じtickerを別の利用者が独立して登録できる)
aws dynamodb create-table \
  --table-name watchlist \
  --attribute-definitions AttributeName=owner_token,AttributeType=S AttributeName=ticker,AttributeType=S \
  --key-schema AttributeName=owner_token,KeyType=HASH AttributeName=ticker,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

aws dynamodb create-table \
  --table-name signal_history \
  --attribute-definitions AttributeName=ticker,AttributeType=S AttributeName=date,AttributeType=S \
  --key-schema AttributeName=ticker,KeyType=HASH AttributeName=date,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

aws dynamodb create-table \
  --table-name app_settings \
  --attribute-definitions AttributeName=key,AttributeType=S \
  --key-schema AttributeName=key,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

aws dynamodb create-table \
  --table-name approved_tokens \
  --attribute-definitions AttributeName=token,AttributeType=S \
  --key-schema AttributeName=token,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

echo "=== 2. SESメールアドレス検証(自分でメール内リンクをクリックする必要あり) ==="
aws ses verify-email-identity --email-address "$SES_SENDER" --region $AWS_REGION
echo "→ $SES_SENDER 宛に確認メールが届きます。リンクをクリックして検証を完了してください。"

echo "=== 3. IAMロール作成 ==="
cat > /tmp/lambda-trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}
EOF

aws iam create-role --role-name stock-report-batch-role \
  --assume-role-policy-document file:///tmp/lambda-trust-policy.json || echo "(既に存在する場合はスキップ)"

cat > /tmp/batch-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["dynamodb:GetItem","dynamodb:PutItem","dynamodb:Scan","dynamodb:Query"],
     "Resource": [
       "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/watchlist",
       "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/signal_history"
     ]},
    {"Effect": "Allow", "Action": ["dynamodb:GetItem","dynamodb:PutItem"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/app_settings"},
    {"Effect": "Allow", "Action": ["dynamodb:GetItem","dynamodb:Scan"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/approved_tokens"},
    {"Effect": "Allow", "Action": ["ses:SendEmail"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"], "Resource": "*"}
  ]
}
EOF
aws iam put-role-policy --role-name stock-report-batch-role \
  --policy-name batch-policy --policy-document file:///tmp/batch-policy.json

aws iam create-role --role-name stock-report-register-role \
  --assume-role-policy-document file:///tmp/lambda-trust-policy.json || echo "(既に存在する場合はスキップ)"

cat > /tmp/register-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["dynamodb:PutItem","dynamodb:DeleteItem","dynamodb:Scan","dynamodb:Query"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/watchlist"},
    {"Effect": "Allow", "Action": ["dynamodb:GetItem","dynamodb:PutItem","dynamodb:DeleteItem","dynamodb:Scan"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/approved_tokens"},
    {"Effect": "Allow", "Action": ["dynamodb:GetItem","dynamodb:PutItem","dynamodb:DeleteItem"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/app_settings"},
    {"Effect": "Allow", "Action": ["lambda:InvokeFunction"],
     "Resource": "arn:aws:lambda:$AWS_REGION:$ACCOUNT_ID:function:daily-batch"},
    {"Effect": "Allow", "Action": ["ses:SendEmail"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"], "Resource": "*"}
  ]
}
EOF
aws iam put-role-policy --role-name stock-report-register-role \
  --policy-name register-policy --policy-document file:///tmp/register-policy.json

# EventBridge Scheduler がdaily-batchを起動するためのロール(信頼先はscheduler)
cat > /tmp/scheduler-trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "scheduler.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}
EOF
aws iam create-role --role-name scheduler-invoke-batch-role \
  --assume-role-policy-document file:///tmp/scheduler-trust-policy.json || echo "(既に存在する場合はスキップ)"

cat > /tmp/scheduler-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "lambda:InvokeFunction",
     "Resource": "arn:aws:lambda:$AWS_REGION:$ACCOUNT_ID:function:daily-batch"}
  ]
}
EOF
aws iam put-role-policy --role-name scheduler-invoke-batch-role \
  --policy-name invoke-batch-policy --policy-document file:///tmp/scheduler-policy.json

echo "IAMロール反映待ち(10秒)..."
sleep 10

echo "=== 4. 管理者用トークン発行(自分専用、status=approvedで即利用可能) ==="
# 一般利用者はダブルオプトイン(メール確認)で自分自身のトークンを取得する。
# これは運用者自身の分だけ、承認済みとして直接発行する。
REGISTER_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
aws dynamodb put-item \
  --table-name approved_tokens \
  --item "{\"token\":{\"S\":\"$REGISTER_TOKEN\"},\"email\":{\"S\":\"$SES_SENDER\"},\"status\":{\"S\":\"approved\"}}" \
  --region $AWS_REGION
echo "あなた専用トークン(このURLを他人に共有しないこと): $REGISTER_TOKEN"

echo "=== 5. Lambda Layer 公開 ==="
LAYER_ARN=$(aws lambda publish-layer-version \
  --layer-name stock-report-deps \
  --zip-file fileb://$LAYER_ZIP \
  --compatible-runtimes python3.12 \
  --region $AWS_REGION --query LayerVersionArn --output text)
echo "Layer ARN: $LAYER_ARN"

echo "=== 6. daily-batch Lambda 作成 ==="
rm -f daily_batch.zip
zip daily_batch.zip $DAILY_BATCH_FILES > /dev/null

aws lambda create-function \
  --function-name daily-batch \
  --runtime python3.12 \
  --role "arn:aws:iam::$ACCOUNT_ID:role/stock-report-batch-role" \
  --handler handler.handler \
  --zip-file fileb://daily_batch.zip \
  --timeout 900 --memory-size 512 \
  --layers "$LAYER_ARN" \
  --environment "Variables={SES_SENDER=$SES_SENDER,SES_RECIPIENT=$SES_RECIPIENT,REGISTER_FUNCTION_URL=PLACEHOLDER,TOP_N=10}" \
  --region $AWS_REGION || \
  aws lambda update-function-code --function-name daily-batch \
    --zip-file fileb://daily_batch.zip --region $AWS_REGION

echo "=== 7. register-stock Lambda 作成 + Function URL 発行 ==="
rm -f register.zip
(cd "$REGISTER_DIR" && zip "$OLDPWD/register.zip" handler.py > /dev/null)
zip -j register.zip config.py > /dev/null

aws lambda create-function \
  --function-name register-stock \
  --runtime python3.12 \
  --role "arn:aws:iam::$ACCOUNT_ID:role/stock-report-register-role" \
  --handler handler.handler \
  --zip-file fileb://register.zip \
  --timeout 30 --memory-size 128 \
  --environment "Variables={MAX_WATCHLIST_SIZE=5,DAILY_BATCH_FUNCTION=daily-batch,SES_SENDER=$SES_SENDER,SES_RECIPIENT=$SES_RECIPIENT,REGISTER_FUNCTION_URL=PLACEHOLDER}" \
  --region $AWS_REGION || \
  aws lambda update-function-code --function-name register-stock \
    --zip-file fileb://register.zip --region $AWS_REGION

# 公開URLの濫用でアカウントの同時実行枠(上限10)を食い潰さないよう上限を設ける
aws lambda put-function-concurrency \
  --function-name register-stock \
  --reserved-concurrent-executions 2 \
  --region $AWS_REGION

aws lambda create-function-url-config \
  --function-name register-stock \
  --auth-type NONE \
  --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

# 2025年10月以降のFunction URLは以下の2つの許可が両方必要。
# 既存の文が残っていると add-permission は上書きせず失敗するため、
# 先に削除してから追加する(冪等)。
aws lambda remove-permission --function-name register-stock \
  --statement-id public-url-access --region $AWS_REGION 2>/dev/null || true
aws lambda add-permission \
  --function-name register-stock \
  --action lambda:InvokeFunctionUrl \
  --principal "*" \
  --function-url-auth-type NONE \
  --statement-id public-url-access \
  --region $AWS_REGION

aws lambda remove-permission --function-name register-stock \
  --statement-id public-url-invoke-function --region $AWS_REGION 2>/dev/null || true
aws lambda add-permission \
  --function-name register-stock \
  --action lambda:InvokeFunction \
  --principal "*" \
  --statement-id public-url-invoke-function \
  --region $AWS_REGION

REGISTER_URL=$(aws lambda get-function-url-config --function-name register-stock \
  --region $AWS_REGION --query FunctionUrl --output text)
echo "登録用URL: $REGISTER_URL"

echo "=== 8. daily-batch・register-stock双方の環境変数にREGISTER_URLを反映 ==="
# --timeoutも合わせて指定: create-function側の変更は既にデプロイ済みの関数には
# 反映されない(create-functionは既存時にupdate-function-codeにフォールバックし
# コードしか更新しない)ため、再デプロイ時にここで確実にタイムアウトを揃える
# (TODO#8 S&P500導入前の事前準備、900秒はLambdaの上限)
aws lambda update-function-configuration \
  --function-name daily-batch \
  --timeout 900 \
  --environment "Variables={SES_SENDER=$SES_SENDER,SES_RECIPIENT=$SES_RECIPIENT,REGISTER_FUNCTION_URL=$REGISTER_URL,TOP_N=10}" \
  --region $AWS_REGION

# register-stock自身も、申込み確認メールのリンク生成に自分のURLを知る必要がある(自己参照)
aws lambda update-function-configuration \
  --function-name register-stock \
  --environment "Variables={MAX_WATCHLIST_SIZE=5,DAILY_BATCH_FUNCTION=daily-batch,SES_SENDER=$SES_SENDER,SES_RECIPIENT=$SES_RECIPIENT,REGISTER_FUNCTION_URL=$REGISTER_URL}" \
  --region $AWS_REGION

echo "=== 9. EventBridge Scheduler 設定(毎日 米東部17時=UTC22時 平日) ==="
aws scheduler create-schedule \
  --name daily-stock-report \
  --schedule-expression "cron(0 22 ? * MON-FRI *)" \
  --flexible-time-window '{"Mode": "OFF"}' \
  --target "{\"Arn\":\"arn:aws:lambda:$AWS_REGION:$ACCOUNT_ID:function:daily-batch\",\"RoleArn\":\"arn:aws:iam::$ACCOUNT_ID:role/scheduler-invoke-batch-role\"}" \
  --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

echo "=== 10. CloudWatch Alarm + SNS通知(daily-batchのエラー検知、TODO#5) ==="
# TODO#1のSES直接通知が動かないケース(Lambda自体が起動できない等)への二重の安全網
ALARM_TOPIC_ARN=$(aws sns create-topic --name daily-batch-alarm-topic --region $AWS_REGION --query TopicArn --output text)
aws sns subscribe --topic-arn $ALARM_TOPIC_ARN --protocol email \
  --notification-endpoint "$SES_RECIPIENT" --region $AWS_REGION > /dev/null
echo "→ $SES_RECIPIENT 宛にSNS購読確認メールが届きます。リンクをクリックしないとアラーム通知が届きません。"

aws cloudwatch put-metric-alarm \
  --alarm-name daily-batch-errors \
  --alarm-description "daily-batch Lambdaがエラー終了した場合に通知(SES通知経路自体が動かないケースの二重の安全網)" \
  --namespace AWS/Lambda --metric-name Errors \
  --dimensions Name=FunctionName,Value=daily-batch \
  --statistic Sum --period 300 --evaluation-periods 1 --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --alarm-actions $ALARM_TOPIC_ARN --treat-missing-data notBreaching \
  --region $AWS_REGION

echo ""
echo "✅ 構築完了。残タスク:"
echo "  1. $SES_SENDER に届く確認メールのリンクをクリック(SES検証)"
echo "  2. サンドボックスモードの場合、$SES_RECIPIENT も同様に verify-email-identity で検証すること"
echo "  3. 動作確認: aws lambda invoke --function-name daily-batch --region $AWS_REGION /tmp/out.json && cat /tmp/out.json"
echo "  4. ブラウザで ${REGISTER_URL}?token=${REGISTER_TOKEN} を開いて登録フォームが表示されることを確認"
echo "  5. あなた専用トークン(${REGISTER_TOKEN})は必ず控えておくこと。再表示は次で可能:"
echo "     aws dynamodb scan --table-name approved_tokens --region $AWS_REGION"
echo "  6. 一般利用者は ${REGISTER_URL} (tokenなし)から自分でメールアドレスを申し込める"
echo "  7. SES本番アクセスが未申請の場合、他人にメールを送るには申請が必要:"
echo "     aws sesv2 put-account-details --mail-type TRANSACTIONAL --website-url <URL> \\"
echo "       --use-case-description <説明> --production-access-enabled --region $AWS_REGION"
