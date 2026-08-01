#!/bin/bash
# ===========================================================
# 株式レポートサービス AWS一括構築スクリプト
# 前提: aws configure 済み（AWS CLIに認証情報が設定済み）
# 使い方: 下記の変数を書き換えてから bash deploy.sh を実行
# ===========================================================
set -e

# ---- 事前に書き換えてください ----
export AWS_REGION="us-east-1"
export SES_SENDER="mohatose4929@gmail.com"      # SESで検証済みにするメールアドレス
export SES_RECIPIENT="mohatose4929@gmail.com"   # レポート送付先(サンドボックスなら要検証)
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
# -----------------------------------

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

echo "=== 2. SESメールアドレス検証(自分でメール内リンクをクリックする必要あり) ==="
aws ses verify-email-identity --email-address "$SES_SENDER" --region $AWS_REGION
echo "→ $SES_SENDER 宛に確認メールが届きます。リンクをクリックして検証を完了してください。"

echo "=== 3. IAMロール作成 ==="
cat > /tmp/trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}
EOF

aws iam create-role --role-name stock-report-batch-role \
  --assume-role-policy-document file:///tmp/trust-policy.json || echo "(既に存在する場合はスキップ)"

cat > /tmp/batch-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["dynamodb:GetItem","dynamodb:PutItem","dynamodb:Scan"],
     "Resource": [
       "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/watchlist",
       "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/signal_history"
     ]},
    {"Effect": "Allow", "Action": ["ses:SendEmail"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"], "Resource": "*"}
  ]
}
EOF
aws iam put-role-policy --role-name stock-report-batch-role \
  --policy-name batch-policy --policy-document file:///tmp/batch-policy.json

aws iam create-role --role-name stock-report-register-role \
  --assume-role-policy-document file:///tmp/trust-policy.json || echo "(既に存在する場合はスキップ)"

cat > /tmp/register-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["dynamodb:PutItem"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/watchlist"},
    {"Effect": "Allow", "Action": ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"], "Resource": "*"}
  ]
}
EOF
aws iam put-role-policy --role-name stock-report-register-role \
  --policy-name register-policy --policy-document file:///tmp/register-policy.json

echo "IAMロード反映待ち(10秒)..."
sleep 10

echo "=== 4. Lambda Layer 公開 ==="
LAYER_ARN=$(aws lambda publish-layer-version \
  --layer-name stock-report-deps \
  --zip-file fileb://layer/layer.zip \
  --compatible-runtimes python3.12 \
  --region $AWS_REGION --query LayerVersionArn --output text)
echo "Layer ARN: $LAYER_ARN"

echo "=== 5. daily-batch Lambda 作成 ==="
cd lambda_daily_batch && zip -r ../daily_batch.zip . -x "*.pyc" > /dev/null && cd ..

aws lambda create-function \
  --function-name daily-batch \
  --runtime python3.12 \
  --role "arn:aws:iam::$ACCOUNT_ID:role/stock-report-batch-role" \
  --handler handler.handler \
  --zip-file fileb://daily_batch.zip \
  --timeout 300 --memory-size 512 \
  --layers "$LAYER_ARN" \
  --environment "Variables={SES_SENDER=$SES_SENDER,SES_RECIPIENT=$SES_RECIPIENT,REGISTER_FUNCTION_URL=PLACEHOLDER}" \
  --region $AWS_REGION || echo "(既に存在する場合は update-function-code を使ってください)"

echo "=== 6. register-stock Lambda 作成 + Function URL 発行 ==="
cd lambda_register && zip -r ../register.zip . -x "*.pyc" > /dev/null && cd ..

aws lambda create-function \
  --function-name register-stock \
  --runtime python3.12 \
  --role "arn:aws:iam::$ACCOUNT_ID:role/stock-report-register-role" \
  --handler handler.handler \
  --zip-file fileb://register.zip \
  --timeout 10 --memory-size 128 \
  --region $AWS_REGION || echo "(既に存在する場合は update-function-code を使ってください)"

aws lambda create-function-url-config \
  --function-name register-stock \
  --auth-type NONE \
  --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

aws lambda add-permission \
  --function-name register-stock \
  --action lambda:InvokeFunctionUrl \
  --principal "*" \
  --function-url-auth-type NONE \
  --statement-id public-url-access \
  --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

REGISTER_URL=$(aws lambda get-function-url-config --function-name register-stock \
  --region $AWS_REGION --query FunctionUrl --output text)
echo "登録用URL: $REGISTER_URL"

echo "=== 7. daily-batch の環境変数にREGISTER_URLを反映 ==="
aws lambda update-function-configuration \
  --function-name daily-batch \
  --environment "Variables={SES_SENDER=$SES_SENDER,SES_RECIPIENT=$SES_RECIPIENT,REGISTER_FUNCTION_URL=$REGISTER_URL}" \
  --region $AWS_REGION

echo "=== 8. EventBridge Scheduler 設定(毎日 米東部17時=UTC22時 平日) ==="
aws scheduler create-schedule \
  --name daily-stock-report \
  --schedule-expression "cron(0 22 ? * MON-FRI *)" \
  --flexible-time-window '{"Mode": "OFF"}' \
  --target "{\"Arn\":\"arn:aws:lambda:$AWS_REGION:$ACCOUNT_ID:function:daily-batch\",\"RoleArn\":\"arn:aws:iam::$ACCOUNT_ID:role/stock-report-batch-role\"}" \
  --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

echo ""
echo "✅ 構築完了。残タスク:"
echo "  1. $SES_SENDER に届く確認メールのリンクをクリック(SES検証)"
echo "  2. サンドボックスモードの場合、$SES_RECIPIENT も同様に verify-email-identity で検証すること"
echo "  3. 動作確認: aws lambda invoke --function-name daily-batch --region $AWS_REGION /tmp/out.json && cat /tmp/out.json"
