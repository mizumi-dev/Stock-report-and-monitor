#!/bin/bash
# ===========================================================
# landing-preview Lambda 構築スクリプト(2026-08-06)
#
# メールアドレスを登録したくない訪問者向けに、短期・中期・長期の選定レポートを
# 登録不要でその場で閲覧できるページ(mnt/user-data/outputs/aws_deploy/lambda_preview)
# を、register-stockとは完全に独立したLambda・Function URLとしてデプロイする。
#
# 意図的に本番ドメイン(pegsignal.com/en.pegsignal.com)には載せず、Lambda Function
# URLのまま(未公開・非リンクのURL)にしておく。CloudFrontに載せて初めて一般公開
# したことになる設計のため、このスクリプトの実行だけでは「公開」にはならない。
#
# 前提: deploy.shで本体(daily-batch・register-stock)が構築済みであること
#       (app_settingsテーブル・register-stockのFunction URLを読み取り専用で使う)
# ===========================================================
set -euo pipefail

export AWS_REGION="us-east-1"
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
PREVIEW_DIR="mnt/user-data/outputs/aws_deploy/lambda_preview"

REGISTER_URL=$(aws lambda get-function-url-config --function-name register-stock \
  --region $AWS_REGION --query FunctionUrl --output text)
echo "register-stock URL(リンク先として埋め込む): $REGISTER_URL"

echo "=== 1. IAMロール作成(読み取り専用: app_settingsのGetItemのみ) ==="
cat > /tmp/preview-lambda-trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}
EOF
aws iam create-role --role-name landing-preview-role \
  --assume-role-policy-document file:///tmp/preview-lambda-trust-policy.json || echo "(既に存在する場合はスキップ)"

cat > /tmp/preview-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["dynamodb:GetItem"],
     "Resource": "arn:aws:dynamodb:$AWS_REGION:$ACCOUNT_ID:table/app_settings"},
    {"Effect": "Allow", "Action": ["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"], "Resource": "*"}
  ]
}
EOF
aws iam put-role-policy --role-name landing-preview-role \
  --policy-name preview-policy --policy-document file:///tmp/preview-policy.json

echo "IAMロール反映待ち(10秒)..."
sleep 10

echo "=== 2. Lambda作成 + Function URL発行 ==="
rm -f preview.zip
(cd "$PREVIEW_DIR" && zip "$OLDPWD/preview.zip" handler.py og-image.jpg > /dev/null)
zip -j preview.zip config.py report.py > /dev/null

aws lambda create-function \
  --function-name landing-preview \
  --runtime python3.12 \
  --role "arn:aws:iam::$ACCOUNT_ID:role/landing-preview-role" \
  --handler handler.handler \
  --zip-file fileb://preview.zip \
  --timeout 10 --memory-size 128 \
  --environment "Variables={REGISTER_URL=$REGISTER_URL}" \
  --region $AWS_REGION || \
  aws lambda update-function-code --function-name landing-preview \
    --zip-file fileb://preview.zip --region $AWS_REGION

aws lambda update-function-configuration \
  --function-name landing-preview \
  --environment "Variables={REGISTER_URL=$REGISTER_URL}" \
  --region $AWS_REGION

# 濫用でアカウントの同時実行枠を食い潰さないよう上限を設けたいが、既存のLambda群
# (register-stock・daily-batch)が既に予約済みで、アカウント全体の未予約枠の下限
# (10)を割り込むため設定できない(2026-08-06確認)。テスト用途で流量が少ないうちは
# 未設定のまま運用し、公開後に予約枠の再配分を検討する
aws lambda put-function-concurrency \
  --function-name landing-preview \
  --reserved-concurrent-executions 2 \
  --region $AWS_REGION || echo "(同時実行予約はアカウント全体の空き枠不足でスキップされる場合がある)"

aws lambda create-function-url-config \
  --function-name landing-preview \
  --auth-type NONE \
  --region $AWS_REGION || echo "(既に存在する場合はスキップ)"

# 2025年10月以降のFunction URLは以下の2つの許可が両方必要(register-stockと同じ理由)
aws lambda remove-permission --function-name landing-preview \
  --statement-id public-url-access --region $AWS_REGION 2>/dev/null || true
aws lambda add-permission \
  --function-name landing-preview \
  --action lambda:InvokeFunctionUrl \
  --principal "*" \
  --function-url-auth-type NONE \
  --statement-id public-url-access \
  --region $AWS_REGION

aws lambda remove-permission --function-name landing-preview \
  --statement-id public-url-invoke-function --region $AWS_REGION 2>/dev/null || true
aws lambda add-permission \
  --function-name landing-preview \
  --action lambda:InvokeFunction \
  --principal "*" \
  --statement-id public-url-invoke-function \
  --region $AWS_REGION

PREVIEW_URL=$(aws lambda get-function-url-config --function-name landing-preview \
  --region $AWS_REGION --query FunctionUrl --output text)

echo ""
echo "✅ 構築完了(テスト用、本番ドメインには未接続)"
echo "  プレビューURL: $PREVIEW_URL"
echo "  このURLはpegsignal.com/en.pegsignal.comのどこからもリンクされていません。"
echo "  一般公開する場合は、CloudFrontへのオリジン追加等が別途必要です。"
