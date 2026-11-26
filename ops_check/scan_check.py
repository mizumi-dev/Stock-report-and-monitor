"""一時的な確認用(このブランチ専用、developにはマージしない)。
本番と同じhandler._dispatch({"_test_scan": ...})を、偽DynamoDBで全銘柄に対して実行する。"""
import sys, os, time, json
sys.path.insert(0, "tests"); sys.path.insert(0, ".")
os.environ.update(SES_SENDER="x@example.com", SES_RECIPIENT="x@example.com", REGISTER_FUNCTION_URL="https://example.com/")
from unittest import mock
import boto3; boto3.resource = mock.MagicMock(); boto3.client = mock.MagicMock()
import handler
from fake_dynamodb import FakeDynamoDBResource
fake = FakeDynamoDBResource({"app_settings": ("key", None)})
handler.dynamodb = fake; handler.SETTINGS_TABLE = "app_settings"
calls = {"n": 0, "fail": 0}
orig = handler.get_growth_estimate
def counting(t):
    calls["n"] += 1
    try:
        return orig(t)
    except Exception:
        calls["fail"] += 1
        raise
handler.get_growth_estimate = counting
ok = True
for mode, universe in (("MID", "SP500"), ("MID", "NASDAQ100"), ("SHORT", "SP500")):
    t0 = time.time()
    try:
        body = json.loads(handler._dispatch({"_test_scan": {"mode": mode, "universe": universe}})["body"])
        print(f"RESULT {mode} {universe} OK {json.dumps(body, ensure_ascii=False)}", flush=True)
    except Exception as e:
        ok = False
        print(f"RESULT {mode} {universe} FAILED after {time.time()-t0:.0f}s: {e}", flush=True)
    print(f"RESULT growth-estimate calls so far: {calls}", flush=True)
sys.exit(0 if ok else 1)
