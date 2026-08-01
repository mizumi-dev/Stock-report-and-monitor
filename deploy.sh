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
export SES_SENDER="mohatose4929@gmail.com"      # SESで検証済みにするメールアドレス
export SES_RECIPIENT="mohatose4929@gmail.com"   # レポート送付先(サンドボックスなら要検証)
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
# -----------------------------------

# リポジトリのレイアウト（このスクリプトからの相対パス）
DAILY_BATCH_FILES="handler.py config.py signals.py data_sources.py ranking.py report.py"
REGISTER_DIR="mnt/user-data/outputs/aws_deploy/lambda_register"
LAYER_ZIP="layer.zip"

echo "=== 1. DynamoDBテーブル作成 ==="
aws dynamodb create-table \
  --table-name watchlist \
  --attribute-definitions AttributeName=ticker,AttributeType=S \
  --key-schema AttributeName=ticker,KeyType=HASH \
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

aws iam create-role --role-name lambda-daily-batch-role \
  --assume-role-policy-document file:///tmp/lambda-trust-policy.json || echo "(既に存在する場合はスキップ)"

cat > /tmp/batch-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["dynamodb:GetItem","dynamodb:PutItem","dynamodb:Scan"],
     "Resource": [
       "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/watchlist",
       "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/signal_history"
     ]},
    {"Effect": "Allow", "Action": ["dynamodb:GetItem"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/app_settings"},
    {"Effect": "Allow", "Action": ["ses:SendEmail"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"], "Resource": "*"}
  ]
}
EOF
aws iam put-role-policy --role-name lambda-daily-batch-role \
  --policy-name batch-policy --policy-document file:///tmp/batch-policy.json

aws iam create-role --role-name lambda-register-stock-role \
  --assume-role-policy-document file:///tmp/lambda-trust-policy.json || echo "(既に存在する場合はスキップ)"

cat > /tmp/register-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["dynamodb:PutItem","dynamodb:DeleteItem","dynamodb:Scan"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/watchlist"},
    {"Effect": "Allow", "Action": ["dynamodb:GetItem"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/approved_tokens"},
    {"Effect": "Allow", "Action": ["dynamodb:GetItem","dynamodb:PutItem"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/app_settings"},
    {"Effect": "Allow", "Action": ["lambda:InvokeFunction"],
     "Resource": "arn:aws:lambda:$AWS_REGION:$ACCOUNT_ID:function:daily-batch"},
    {"Effect": "Allow", "Action": ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"], "Resource": "*"}
  ]
}
EOF
aws iam put-role-policy --role-name lambda-register-stock-role \
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

echo "=== 4. 招待トークン発行(承認済み利用者= SES_SENDER 用) ==="
REGISTER_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
aws dynamodb put-item \
  --table-name approved_tokens \
  --item "{\"token\":{\"S\":\"$REGISTER_TOKEN\"},\"email\":{\"S\":\"$SES_SENDER\"}}" \
  --region $AWS_REGION
echo "招待トークン(このURLを本人以外に共有しないこと): $REGISTER_TOKEN"

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
  --role "arn:aws:iam::$ACCOUNT_ID:role/lambda-daily-batch-role" \
  --handler handler.handler \
  --zip-file fileb://daily_batch.zip \
  --timeout 300 --memory-size 512 \
  --layers "$LAYER_ARN" \
  --environment "Variables={SES_SENDER=$SES_SENDER,SES_RECIPIENT=$SES_RECIPIENT,REGISTER_FUNCTION_URL=PLACEHOLDER,REGISTER_TOKEN=$REGISTER_TOKEN,TOP_N=3}" \
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
  --role "arn:aws:iam::$ACCOUNT_ID:role/lambda-register-stock-role" \
  --handler handler.handler \
  --zip-file fileb://register.zip \
  --timeout 30 --memory-size 128 \
  --environment "Variables={MAX_WATCHLIST_SIZE=5,DAILY_BATCH_FUNCTION=daily-batch}" \
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

echo "=== 8. daily-batch の環境変数にREGISTER_URLを反映 ==="
aws lambda update-function-configuration \
  --function-name daily-batch \
  --environment "Variables={SES_SENDER=$SES_SENDER,SES_RECIPIENT=$SES_RECIPIENT,REGISTER_FUNCTION_URL=$REGISTER_URL,REGISTER_TOKEN=$REGISTER_TOKEN,TOP_N=3}" \
  --region $AWS_REGION

echo "=== 9. EventBridge Scheduler 設定(毎日 米東部17時=UTC22時 平日) ==="
aws scheduler create-schedule \
  --name daily-stock-report \
  --schedule-expression "cron(0 22 ? * MON-FRI *)" \
  --flexible-time-window '{"Mode": "OFF"}' \
  --target "{\"Arn\":\"arn:aws:lambda:$AWS_REGION:$ACCOUNT_ID:function:daily-batch\",\"RoleArn\":\"arn:aws:iam::$ACCOUNT_ID:role/scheduler-invoke-batch-role\"}" \
  --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

echo ""
echo "✅ 構築完了。残タスク:"
echo "  1. $SES_SENDER に届く確認メールのリンクをクリック(SES検証)"
echo "  2. サンドボックスモードの場合、$SES_RECIPIENT も同様に verify-email-identity で検証すること"
echo "  3. 動作確認: aws lambda invoke --function-name daily-batch --region $AWS_REGION /tmp/out.json && cat /tmp/out.json"
echo "  4. ブラウザで ${REGISTER_URL}?token=${REGISTER_TOKEN} を開いて登録フォームが表示されることを確認"
echo "  5. 招待トークン(${REGISTER_TOKEN})は必ず控えておくこと。再表示は次で可能:"
echo "     aws dynamodb scan --table-name approved_tokens --region $AWS_REGION"
