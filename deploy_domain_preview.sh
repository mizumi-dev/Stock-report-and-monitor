#!/bin/bash
# preview.pegsignal.com を landing-preview の Lambda Function URL に向けるための
# ACM証明書・CloudFront・Route53設定。
#
# deploy_domain.sh(pegsignal.com本体)・deploy_domain_en.sh(en.pegsignal.com)と
# 同じパターン: pegsignal.com側の証明書・ディストリビューションには一切手を加えず、
# 別のACM証明書・別のCloudFrontディストリビューションを追加するだけの独立構成
# (expert-panel-reviewでの検討結果、既存の本番導線への影響(blast radius)を
# ゼロにするため、pegsignal.com本体のディストリビューションは変更しない方針とした)。
#
# 前提: pegsignal.com はRoute53で取得済み・ホストゾーンが存在すること
#       (preview.はそのホストゾーン内のサブドメインとして追加する)。
#       landing-preview LambdaのFunction URLは既に発行済みであること。
#
# 注意: ACM証明書のDNS検証やCloudFrontの配信開始には数分〜数十分かかることがある。
#       実行後は必ず出力されるURLで動作確認すること。
set -e

DOMAIN=preview.pegsignal.com
PARENT_DOMAIN=pegsignal.com
# CloudFront用のACM証明書はus-east-1固定(バージニア北部)という制約がある
CERT_REGION=us-east-1
LAMBDA_REGION=${AWS_REGION:-us-east-1}

echo "=== 1. landing-previewのFunction URLを取得 ==="
PREVIEW_URL=$(aws lambda get-function-url-config --function-name landing-preview \
  --region "$LAMBDA_REGION" --query FunctionUrl --output text)
PREVIEW_HOST=$(echo "$PREVIEW_URL" | sed -E 's#https://([^/]+)/?#\1#')
echo "Origin: $PREVIEW_HOST"

echo "=== 2. ${PARENT_DOMAIN} のホストゾーンIDを取得(preview.は同じゾーン内のサブドメイン) ==="
ZONE_ID=$(aws route53 list-hosted-zones-by-name --dns-name "$PARENT_DOMAIN" \
  --query "HostedZones[0].Id" --output text | sed 's#/hostedzone/##')
if [ -z "$ZONE_ID" ] || [ "$ZONE_ID" = "None" ]; then
  echo "ホストゾーンが見つかりません。${PARENT_DOMAIN} がRoute53で取得済みか確認してください。"
  exit 1
fi
echo "Zone ID: $ZONE_ID"

echo "=== 3. ACM証明書をリクエスト(DNS検証、us-east-1固定) ==="
# 再実行時に重複した証明書を発行しないよう、既存の証明書があれば再利用する
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
# 再実行時に重複したディストリビューションを作らないよう、$DOMAIN を
# Aliasに持つ既存のディストリビューションがあれば再利用する
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
  # 本当は静的に近いページなのでキャッシュを効かせたいところだが、選定スナップショットが
  # 日次更新されること・実装をpegsignal.com本体と揃えてシンプルに保つことを優先した
  DIST_CONFIG=$(python3 -c "
import json
config = {
    'CallerReference': 'pegsignal-preview-$(date +%s)',
    'Comment': 'preview.pegsignal.com -> landing-preview Lambda Function URL',
    'Enabled': True,
    'Aliases': {'Quantity': 1, 'Items': ['$DOMAIN']},
    'Origins': {
        'Quantity': 1,
        'Items': [{
            'Id': 'landing-preview-origin',
            'DomainName': '$PREVIEW_HOST',
            'CustomOriginConfig': {
                'HTTPPort': 80, 'HTTPSPort': 443,
                'OriginProtocolPolicy': 'https-only',
                'OriginSslProtocols': {'Quantity': 1, 'Items': ['TLSv1.2']},
            },
        }],
    },
    'DefaultCacheBehavior': {
        'TargetOriginId': 'landing-preview-origin',
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

echo "=== 7. preview.pegsignal.com をCloudFrontへのALIASレコードに設定 ==="
# CloudFrontのALIASターゲットのホストゾーンIDは固定値(Z2FDTNDATAQYW2)
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
echo "  1. https://${DOMAIN}/ にアクセスして選定レポートのプレビューページが表示されることを確認"
echo "  2. https://${PARENT_DOMAIN}/ (申込みフォーム)が引き続き正常に表示されることを確認"
echo "     (このスクリプトはpegsignal.com本体のディストリビューションには一切触れていない)"
echo "  3. register-stockのPREVIEW_URL環境変数を https://${DOMAIN}/ に設定すると、"
echo "     申込み画面に「登録せずに見る」リンクが表示される"
