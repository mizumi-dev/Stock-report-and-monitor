#!/bin/bash
# ===========================================================
# 開発環境(develop環境)構築スクリプト(2026-08-06)
#
# 本番(master)と物理的に完全に分離した開発用の3Lambda・4DynamoDBテーブルを
# 構築する。本番データ(実利用者のメールアドレス・監視銘柄・選定スナップショット)
# には一切触れない設計。
#
# 分離が必須な理由:
# - approved_tokensを本番と共有すると、開発環境でのテスト登録が本番daily-batchの
#   get_approved_users()(テーブル全件スキャン)に拾われ、本番のResend経由で
#   実際にメールが送信されてしまう
# - app_settingsのselection_snapshotを本番と共有すると、開発環境の書き込みが
#   本番の「今すぐ送信」ボタンが読む正本を上書きしてしまう(CLAUDE.mdの
#   「バッチが計算し正本を保有、ボタン押下は読み取り専用」の境界を壊す)
#
# 運用: developブランチへのマージでCI/CD(deploy.yml)がこれらのLambdaへ自動
# デプロイする。本番(master)への反映は、ユーザーが明示的にdevelop→masterの
# PRをマージした時のみ発生する。
# ===========================================================
set -euo pipefail

export AWS_REGION="us-east-1"
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
# 本番と同じRESEND_API_KEYを流用する(開発環境専用のResendドメイン・アカウントは
# 用意しない)。開発環境のapproved_tokens-devには開発者が意図的に作成したテスト
# アカウントしか存在しないため、実利用者へ誤送信するリスクは無い
RESEND_API_KEY=$(aws lambda get-function-configuration --function-name register-stock \
  --region $AWS_REGION --query "Environment.Variables.RESEND_API_KEY" --output text)
LAYER_ARN=$(aws lambda get-function-configuration --function-name daily-batch \
  --region $AWS_REGION --query "Layers[0].Arn" --output text)

DAILY_BATCH_FILES="handler.py config.py signals.py data_sources.py ranking.py report.py mailer.py social.py"
REGISTER_DIR="mnt/user-data/outputs/aws_deploy/lambda_register"
PREVIEW_DIR="mnt/user-data/outputs/aws_deploy/lambda_preview"

echo "=== 1. DynamoDBテーブル作成(すべて-devサフィックス) ==="
aws dynamodb create-table \
  --table-name watchlist-dev \
  --attribute-definitions AttributeName=owner_token,AttributeType=S AttributeName=ticker,AttributeType=S \
  --key-schema AttributeName=owner_token,KeyType=HASH AttributeName=ticker,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

aws dynamodb create-table \
  --table-name signal_history-dev \
  --attribute-definitions AttributeName=ticker,AttributeType=S AttributeName=date,AttributeType=S \
  --key-schema AttributeName=ticker,KeyType=HASH AttributeName=date,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

aws dynamodb create-table \
  --table-name app_settings-dev \
  --attribute-definitions AttributeName=key,AttributeType=S \
  --key-schema AttributeName=key,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

aws dynamodb create-table \
  --table-name approved_tokens-dev \
  --attribute-definitions AttributeName=token,AttributeType=S \
  --key-schema AttributeName=token,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

echo "=== 2. IAMロール作成(開発用テーブルのみアクセス可能) ==="
cat > /tmp/dev-lambda-trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}
EOF

aws iam create-role --role-name stock-report-batch-role-dev \
  --assume-role-policy-document file:///tmp/dev-lambda-trust-policy.json || echo "(既に存在する場合はスキップ)"
cat > /tmp/batch-policy-dev.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["dynamodb:GetItem","dynamodb:PutItem","dynamodb:Scan","dynamodb:Query"],
     "Resource": [
       "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/watchlist-dev",
       "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/signal_history-dev"
     ]},
    {"Effect": "Allow", "Action": ["dynamodb:GetItem","dynamodb:PutItem"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/app_settings-dev"},
    {"Effect": "Allow", "Action": ["dynamodb:GetItem","dynamodb:Scan"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/approved_tokens-dev"},
    {"Effect": "Allow", "Action": ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"], "Resource": "*"}
  ]
}
EOF
aws iam put-role-policy --role-name stock-report-batch-role-dev \
  --policy-name batch-policy-dev --policy-document file:///tmp/batch-policy-dev.json

aws iam create-role --role-name stock-report-register-role-dev \
  --assume-role-policy-document file:///tmp/dev-lambda-trust-policy.json || echo "(既に存在する場合はスキップ)"
cat > /tmp/register-policy-dev.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["dynamodb:PutItem","dynamodb:DeleteItem","dynamodb:Scan","dynamodb:Query"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/watchlist-dev"},
    {"Effect": "Allow", "Action": ["dynamodb:GetItem","dynamodb:PutItem","dynamodb:DeleteItem","dynamodb:Scan"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/approved_tokens-dev"},
    {"Effect": "Allow", "Action": ["dynamodb:GetItem","dynamodb:PutItem","dynamodb:DeleteItem"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/app_settings-dev"},
    {"Effect": "Allow", "Action": ["lambda:InvokeFunction"],
     "Resource": "arn:aws:lambda:$AWS_REGION:$ACCOUNT_ID:function:daily-batch-dev"},
    {"Effect": "Allow", "Action": ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"], "Resource": "*"}
  ]
}
EOF
aws iam put-role-policy --role-name stock-report-register-role-dev \
  --policy-name register-policy-dev --policy-document file:///tmp/register-policy-dev.json

aws iam create-role --role-name landing-preview-role-dev \
  --assume-role-policy-document file:///tmp/dev-lambda-trust-policy.json || echo "(既に存在する場合はスキップ)"
cat > /tmp/preview-policy-dev.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["dynamodb:GetItem"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/app_settings-dev"},
    {"Effect": "Allow", "Action": ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"], "Resource": "*"}
  ]
}
EOF
aws iam put-role-policy --role-name landing-preview-role-dev \
  --policy-name preview-policy-dev --policy-document file:///tmp/preview-policy-dev.json

echo "IAMロール反映待ち(15秒)..."
sleep 15

echo "=== 3. daily-batch-dev Lambda作成(EventBridgeスケジュールは組まない、手動起動のみ) ==="
rm -f daily_batch_dev.zip
zip daily_batch_dev.zip $DAILY_BATCH_FILES > /dev/null
aws lambda create-function \
  --function-name daily-batch-dev \
  --runtime python3.12 \
  --role "arn:aws:iam::$ACCOUNT_ID:role/stock-report-batch-role-dev" \
  --handler handler.handler \
  --zip-file fileb://daily_batch_dev.zip \
  --timeout 900 --memory-size 512 \
  --layers "$LAYER_ARN" \
  --environment "Variables={WATCHLIST_TABLE=watchlist-dev,APPROVED_TOKENS_TABLE=approved_tokens-dev,SETTINGS_TABLE=app_settings-dev,SIGNAL_HISTORY_TABLE=signal_history-dev,SES_SENDER=noreply@pegsignal.com,SES_RECIPIENT=amsllr759@gmail.com,RESEND_API_KEY=$RESEND_API_KEY,REGISTER_FUNCTION_URL=PLACEHOLDER,TOP_N=10}" \
  --region $AWS_REGION || \
  aws lambda update-function-code --function-name daily-batch-dev \
    --zip-file fileb://daily_batch_dev.zip --region $AWS_REGION \
    --query '{FunctionName:FunctionName,LastUpdateStatus:LastUpdateStatus}'

echo "=== 4. register-stock-dev Lambda作成 + Function URL発行 ==="
rm -f register_dev.zip
(cd "$REGISTER_DIR" && zip "$OLDPWD/register_dev.zip" handler.py > /dev/null)
zip -j register_dev.zip config.py mailer.py > /dev/null

aws lambda create-function \
  --function-name register-stock-dev \
  --runtime python3.12 \
  --role "arn:aws:iam::$ACCOUNT_ID:role/stock-report-register-role-dev" \
  --handler handler.handler \
  --zip-file fileb://register_dev.zip \
  --timeout 30 --memory-size 128 \
  --environment "Variables={WATCHLIST_TABLE=watchlist-dev,APPROVED_TOKENS_TABLE=approved_tokens-dev,SETTINGS_TABLE=app_settings-dev,MAX_WATCHLIST_SIZE=5,DAILY_BATCH_FUNCTION=daily-batch-dev,SES_SENDER=noreply@pegsignal.com,SES_RECIPIENT=amsllr759@gmail.com,RESEND_API_KEY=$RESEND_API_KEY,REGISTER_FUNCTION_URL=PLACEHOLDER}" \
  --region $AWS_REGION || \
  aws lambda update-function-code --function-name register-stock-dev \
    --zip-file fileb://register_dev.zip --region $AWS_REGION \
    --query '{FunctionName:FunctionName,LastUpdateStatus:LastUpdateStatus}'

aws lambda create-function-url-config --function-name register-stock-dev \
  --auth-type NONE --region $AWS_REGION || echo "(既に存在する場合はスキップ)"
aws lambda remove-permission --function-name register-stock-dev \
  --statement-id public-url-access --region $AWS_REGION 2>/dev/null || true
aws lambda add-permission --function-name register-stock-dev \
  --action lambda:InvokeFunctionUrl --principal "*" --function-url-auth-type NONE \
  --statement-id public-url-access --region $AWS_REGION
aws lambda remove-permission --function-name register-stock-dev \
  --statement-id public-url-invoke-function --region $AWS_REGION 2>/dev/null || true
aws lambda add-permission --function-name register-stock-dev \
  --action lambda:InvokeFunction --principal "*" \
  --statement-id public-url-invoke-function --region $AWS_REGION

REGISTER_DEV_URL=$(aws lambda get-function-url-config --function-name register-stock-dev \
  --region $AWS_REGION --query FunctionUrl --output text)
echo "register-stock-dev URL: $REGISTER_DEV_URL"

echo "=== 5. daily-batch-dev・register-stock-devにREGISTER_FUNCTION_URLを反映 ==="
aws lambda update-function-configuration --function-name daily-batch-dev \
  --environment "Variables={WATCHLIST_TABLE=watchlist-dev,APPROVED_TOKENS_TABLE=approved_tokens-dev,SETTINGS_TABLE=app_settings-dev,SIGNAL_HISTORY_TABLE=signal_history-dev,SES_SENDER=noreply@pegsignal.com,SES_RECIPIENT=amsllr759@gmail.com,RESEND_API_KEY=$RESEND_API_KEY,REGISTER_FUNCTION_URL=$REGISTER_DEV_URL,TOP_N=10}" \
  --region $AWS_REGION \
  --query '{FunctionName:FunctionName,LastUpdateStatus:LastUpdateStatus}'
aws lambda update-function-configuration --function-name register-stock-dev \
  --environment "Variables={WATCHLIST_TABLE=watchlist-dev,APPROVED_TOKENS_TABLE=approved_tokens-dev,SETTINGS_TABLE=app_settings-dev,MAX_WATCHLIST_SIZE=5,DAILY_BATCH_FUNCTION=daily-batch-dev,SES_SENDER=noreply@pegsignal.com,SES_RECIPIENT=amsllr759@gmail.com,RESEND_API_KEY=$RESEND_API_KEY,REGISTER_FUNCTION_URL=$REGISTER_DEV_URL}" \
  --region $AWS_REGION \
  --query '{FunctionName:FunctionName,LastUpdateStatus:LastUpdateStatus}'

echo "=== 6. landing-preview-dev Lambda作成 + Function URL発行 ==="
rm -f preview_dev.zip
(cd "$PREVIEW_DIR" && zip "$OLDPWD/preview_dev.zip" handler.py og-image.jpg og-image-en.jpg icon.png > /dev/null)
zip -j preview_dev.zip config.py report.py > /dev/null

aws lambda create-function \
  --function-name landing-preview-dev \
  --runtime python3.12 \
  --role "arn:aws:iam::$ACCOUNT_ID:role/landing-preview-role-dev" \
  --handler handler.handler \
  --zip-file fileb://preview_dev.zip \
  --timeout 10 --memory-size 128 \
  --environment "Variables={SETTINGS_TABLE=app_settings-dev,REGISTER_URL=$REGISTER_DEV_URL}" \
  --region $AWS_REGION || \
  aws lambda update-function-code --function-name landing-preview-dev \
    --zip-file fileb://preview_dev.zip --region $AWS_REGION \
    --query '{FunctionName:FunctionName,LastUpdateStatus:LastUpdateStatus}'

aws lambda update-function-configuration --function-name landing-preview-dev \
  --environment "Variables={SETTINGS_TABLE=app_settings-dev,REGISTER_URL=$REGISTER_DEV_URL}" \
  --region $AWS_REGION \
  --query '{FunctionName:FunctionName,LastUpdateStatus:LastUpdateStatus}'

aws lambda create-function-url-config --function-name landing-preview-dev \
  --auth-type NONE --region $AWS_REGION || echo "(既に存在する場合はスキップ)"
aws lambda remove-permission --function-name landing-preview-dev \
  --statement-id public-url-access --region $AWS_REGION 2>/dev/null || true
aws lambda add-permission --function-name landing-preview-dev \
  --action lambda:InvokeFunctionUrl --principal "*" --function-url-auth-type NONE \
  --statement-id public-url-access --region $AWS_REGION
aws lambda remove-permission --function-name landing-preview-dev \
  --statement-id public-url-invoke-function --region $AWS_REGION 2>/dev/null || true
aws lambda add-permission --function-name landing-preview-dev \
  --action lambda:InvokeFunction --principal "*" \
  --statement-id public-url-invoke-function --region $AWS_REGION

PREVIEW_DEV_URL=$(aws lambda get-function-url-config --function-name landing-preview-dev \
  --region $AWS_REGION --query FunctionUrl --output text)

echo ""
echo "✅ 開発環境構築完了"
echo "  register-stock-dev URL: $REGISTER_DEV_URL"
echo "  landing-preview-dev URL: $PREVIEW_DEV_URL"
echo "  daily-batch-devはFunction URL無し。手動実行のみ:"
echo "    aws lambda invoke --function-name daily-batch-dev --region $AWS_REGION --payload '{}' /tmp/out.json"
echo "  自分専用の開発用管理者トークンは別途 approved_tokens-dev に登録すること。"
echo "  本番ドメイン・本番テーブルには一切接続していない。"
