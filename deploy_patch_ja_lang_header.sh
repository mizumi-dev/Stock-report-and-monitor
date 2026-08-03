#!/bin/bash
# 既存のpegsignal.com(日本語)向けCloudFrontディストリビューションのオリジンに、
# X-Site-Lang: ja というカスタムヘッダーを追加する一回限りのパッチ。
#
# en.pegsignal.com側はCustomHeadersでX-Site-Lang: enを常時付与する構成にした
# (deploy_domain_en.sh参照)。pegsignal.com側にも対になるX-Site-Lang: jaを
# 明示的に付けておくことで、register-stock Lambda側のロジックを
# 「enヘッダーがあればen、それ以外はja」ではなく「ヘッダーの値をそのまま見る」
# というシンプルな形にできる(ヘッダー欠落時のフォールバックはja扱いにするため、
# 実際には後者でも動くが、対称性のため明示しておく)。
#
# 既にX-Site-Lang: jaが設定済みの場合は何もしない(idempotent)。
set -e

DOMAIN=pegsignal.com

echo "=== ${DOMAIN} のディストリビューションを検索 ==="
DIST_ID=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?contains(Aliases.Items, '$DOMAIN')].Id | [0]" --output text)
if [ -z "$DIST_ID" ] || [ "$DIST_ID" = "None" ]; then
  echo "${DOMAIN} のディストリビューションが見つかりません。deploy_domain.shを先に実行してください。"
  exit 1
fi
echo "Distribution ID: $DIST_ID"

echo "=== 現在の設定を取得 ==="
aws cloudfront get-distribution-config --id "$DIST_ID" > /tmp/ja-dist-config.json
ETAG=$(python3 -c "import json; print(json.load(open('/tmp/ja-dist-config.json'))['ETag'])")

ALREADY_SET=$(python3 -c "
import json
d = json.load(open('/tmp/ja-dist-config.json'))
origin = d['DistributionConfig']['Origins']['Items'][0]
headers = origin.get('CustomHeaders', {}).get('Items', [])
print('yes' if any(h['HeaderName'] == 'X-Site-Lang' and h['HeaderValue'] == 'ja' for h in headers) else 'no')
")

if [ "$ALREADY_SET" = "yes" ]; then
  echo "既にX-Site-Lang: jaが設定済みです。何もしません。"
  exit 0
fi

echo "=== X-Site-Lang: ja を追加した設定を作成 ==="
python3 -c "
import json
d = json.load(open('/tmp/ja-dist-config.json'))
config = d['DistributionConfig']
origin = config['Origins']['Items'][0]
origin['CustomHeaders'] = {
    'Quantity': 1,
    'Items': [{'HeaderName': 'X-Site-Lang', 'HeaderValue': 'ja'}],
}
with open('/tmp/ja-dist-config-updated.json', 'w') as f:
    json.dump(config, f)
"

aws cloudfront update-distribution --id "$DIST_ID" \
  --distribution-config file:///tmp/ja-dist-config-updated.json \
  --if-match "$ETAG"

rm -f /tmp/ja-dist-config.json /tmp/ja-dist-config-updated.json
echo "✅ 完了。反映まで数分かかります。"
