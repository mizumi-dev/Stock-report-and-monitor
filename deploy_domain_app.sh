#!/bin/bash
# app.pegsignal.com を register-stock の Lambda Function URL に向けるための
# ACM証明書・CloudFront・Route53設定。
#
# 背景(2026-08-07、expert-panel-review): register-stockが生成する各種リンク
# (メール本文のレポート即時発行リンク・watchlistフォームのURL等)は現在
# REGISTER_FUNCTION_URL環境変数の値、つまり生のLambda Function URL
# (xxxxx.lambda-url.us-east-1.on.aws)をそのまま使っている。株式サービスの
# メールに生のAWS URLが入っているとフィッシングに見え、信頼性・到達率を
# 損なう。ブランド化されたドメインを用意し、REGISTER_FUNCTION_URLをこちらに
# 切り替える(このスクリプト自体はドメインを用意するだけで、環境変数の更新は
# 別途行う)。
#
# deploy_domain.sh(pegsignal.com本体)・deploy_domain_en.sh(en.pegsignal.com)・
# deploy_domain_preview.sh(preview.pegsignal.com)と同じパターン:
# pegsignal.com側の証明書・ディストリビューションには一切手を加えず、
# 別のACM証明書・別のCloudFrontディストリビューションを追加するだけの独立構成。
# オリジンはpegsignal.com本体と同じregister-stock Function URLなので、
# app.pegsignal.comとpegsignal.comは切替期間中どちらも同時に機能する
# (どちらも同じLambdaを指すため、片方だけが動く・壊れる心配がない)。
#
# 前提: pegsignal.com はRoute53で取得済み・ホストゾーンが存在すること
#       (app.はそのホストゾーン内のサブドメインとして追加する)。
#       register-stock LambdaのFunction URLは既に発行済みであること。
#
# 注意: ACM証明書のDNS検証やCloudFrontの配信開始には数分〜数十分かかることがある。
#       実行後は必ず出力されるURLで動作確認すること。
set -e

DOMAIN=app.pegsignal.com
PARENT_DOMAIN=pegsignal.com
# CloudFront用のACM証明書はus-east-1固定(バージニア北部)という制約がある
CERT_REGION=us-east-1
LAMBDA_REGION=${AWS_REGION:-us-east-1}

echo "=== 1. register-stockのFunction URLを取得 ==="
REGISTER_URL=$(aws lambda get-function-url-config --function-name register-stock \
  --region "$LAMBDA_REGION" --query FunctionUrl --output text)
REGISTER_HOST=$(echo "$REGISTER_URL" | sed -E 's#https://([^/]+)/?#\1#')
echo "Origin: $REGISTER_HOST"

echo "=== 2. ${PARENT_DOMAIN} のホストゾーンIDを取得(app.は同じゾーン内のサブドメイン) ==="
ZONE_ID=$(aws route53 list-hosted-zones-by-name --dns-name "$PARENT_DOMAIN" \
  --query "HostedZones[0].Id" --output text | sed 's#/hostedzone/##')
if [ -z "$ZONE_ID" ] || [ "$ZONE_ID" = "None" ]; then
  echo "ホストゾーンが見つかりません。${PARENT_DOMAIN} がRoute53で取得済みか確認してください。"
  exit 1
fi
echo "Zone ID: $ZONE_ID"

echo "=== 3. ACM証明書をリクエスト(DNS検証、us-east-1固定) ==="
CERT_ARN=$(aws acm list-certificates --region "$CERT_REGION" \
  --query "CertificateSummaryList[?DomainName=='$DOMAIN'].CertificateArn | [0]" --output text)
if [ -z "$CERT_ARN" ] || [ "$CERT_ARN" = "None" ]; then
  CERT_ARN=$(aws acm request-certificate \
    --domain-name "$DOMAIN" \
    --validation-method DNS \
    --region "$CERT_REGION" \
    --query CertificateArn --output text)
  echo "Certificate ARN(新規): $CERT_ARN"
  echo "検証用DNSレコード情報の反映を待機中..."
  sleep 15
else
  echo "Certificate ARN(既存を再利用): $CERT_ARN"
fi

echo "=== 4. 検証用CNAMEレコードをRoute53に投入 ==="
CHANGE_BATCH=$(aws acm describe-certificate --certificate-arn "$CERT_ARN" \
  --region "$CERT_REGION" --query "Certificate.DomainValidationOptions[].ResourceRecord" \
  | python3 -c '
import json, sys
records = json.load(sys.stdin)
changes = [{
    "Action": "UPSERT",
    "ResourceRecordSet": {
        "Name": r["Name"], "Type": r["Type"], "TTL": 300,
        "ResourceRecords": [{"Value": r["Value"]}],
    },
} for r in records]
print(json.dumps({"Changes": changes}))
')
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" \
  --change-batch "$CHANGE_BATCH"

echo "=== 5. 証明書の検証完了を待機(数分〜数十分かかる場合あり) ==="
aws acm wait certificate-validated --certificate-arn "$CERT_ARN" --region "$CERT_REGION"
echo "証明書の検証が完了しました。"

echo "=== 6. CloudFrontディストリビューションを作成 ==="
EXISTING_DIST_ID=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?contains(Aliases.Items, '$DOMAIN')].Id | [0]" --output text)
if [ -n "$EXISTING_DIST_ID" ] && [ "$EXISTING_DIST_ID" != "None" ]; then
  DIST_ID="$EXISTING_DIST_ID"
  DIST_DOMAIN=$(aws cloudfront get-distribution --id "$DIST_ID" \
    --query "Distribution.DomainName" --output text)
  echo "Distribution ID(既存を再利用): $DIST_ID"
  echo "Distribution Domain: $DIST_DOMAIN"
else
  # CachingDisabled + AllViewerExceptHostHeader (AWS管理ポリシー、pegsignal.com本体と同じ)。
  # このアプリは全操作がクエリパラメータ(?token=...等)経由でステートフルなため、
  # キャッシュせずクエリ文字列・ヘッダをそのままoriginへ転送する必要がある
  DIST_CONFIG=$(python3 -c "
import json
config = {
    'CallerReference': 'pegsignal-app-$(date +%s)',
    'Comment': 'app.pegsignal.com -> register-stock Lambda Function URL',
    'Enabled': True,
    'Aliases': {'Quantity': 1, 'Items': ['$DOMAIN']},
    'Origins': {
        'Quantity': 1,
        'Items': [{
            'Id': 'register-stock-origin-app',
            'DomainName': '$REGISTER_HOST',
            'CustomOriginConfig': {
                'HTTPPort': 80, 'HTTPSPort': 443,
                'OriginProtocolPolicy': 'https-only',
                'OriginSslProtocols': {'Quantity': 1, 'Items': ['TLSv1.2']},
            },
        }],
    },
    'DefaultCacheBehavior': {
        'TargetOriginId': 'register-stock-origin-app',
        'ViewerProtocolPolicy': 'redirect-to-https',
        'AllowedMethods': {
            'Quantity': 2, 'Items': ['GET', 'HEAD'],
            'CachedMethods': {'Quantity': 2, 'Items': ['GET', 'HEAD']},
        },
        # CachingDisabled(AWS管理ポリシー、固定ID)
        'CachePolicyId': '4135ea2d-6df8-44a3-9df3-4b5a84be39ad',
        # AllViewerExceptHostHeader(AWS管理ポリシー、固定ID): クエリ文字列全転送
        'OriginRequestPolicyId': 'b689b0a8-53d0-40ab-baf2-68738e2966ac',
    },
    'ViewerCertificate': {
        'ACMCertificateArn': '$CERT_ARN',
        'SSLSupportMethod': 'sni-only',
        'MinimumProtocolVersion': 'TLSv1.2_2021',
    },
}
print(json.dumps(config))
")
  DIST_JSON=$(aws cloudfront create-distribution --distribution-config "$DIST_CONFIG")
  DIST_ID=$(echo "$DIST_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['Distribution']['Id'])")
  DIST_DOMAIN=$(echo "$DIST_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['Distribution']['DomainName'])")
  echo "Distribution ID(新規): $DIST_ID"
  echo "Distribution Domain: $DIST_DOMAIN"
fi

echo "=== 7. app.pegsignal.com をCloudFrontへのALIASレコードに設定 ==="
ALIAS_CHANGE_BATCH=$(python3 -c "
import json
record = {
    'Action': 'UPSERT',
    'ResourceRecordSet': {
        'Name': '$DOMAIN', 'Type': 'A',
        'AliasTarget': {
            'HostedZoneId': 'Z2FDTNDATAQYW2',
            'DNSName': '$DIST_DOMAIN',
            'EvaluateTargetHealth': False,
        },
    },
}
print(json.dumps({'Changes': [record]}))
")
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" \
  --change-batch "$ALIAS_CHANGE_BATCH"

echo ""
echo "✅ 完了。CloudFrontの配信開始まで数分〜数十分かかります。"
echo "  1. https://${DOMAIN}/ にアクセスして申込みフォームが表示されることを確認"
echo "  2. https://${PARENT_DOMAIN}/ が引き続き正常に表示されることを確認"
echo "     (このスクリプトはpegsignal.com本体のディストリビューションには一切触れていない)"
echo "  3. register-stock・daily-batchのREGISTER_FUNCTION_URL環境変数を"
echo "     https://${DOMAIN}/ に更新すると、メール内リンク等がブランド化される"
