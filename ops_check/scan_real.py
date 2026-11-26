"""一時的な確認用(マージしない)。feature/forward-peg-exclusionsの実コードで
handler._dispatch({"_test_scan": ...})を偽DynamoDBで全銘柄に対して実行する。"""
import sys, os, time, json
sys.path.insert(0, "tests"); sys.path.insert(0, ".")
os.environ.update(SES_SENDER="x@example.com", SES_RECIPIENT="x@example.com", REGISTER_FUNCTION_URL="https://example.com/")
from unittest import mock
import boto3; boto3.resource = mock.MagicMock(); boto3.client = mock.MagicMock()
import handler
from fake_dynamodb import FakeDynamoDBResource
import yfinance as yf
print("RESULT yfinance", yf.__version__, flush=True)
fake = FakeDynamoDBResource({"app_settings": ("key", None)})
handler.dynamodb = fake; handler.SETTINGS_TABLE = "app_settings"
handler.TOP_N = 10
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
for mode, universe in (("SHORT", "NASDAQ100"), ("SHORT", "SP500"), ("MID", "NASDAQ100"), ("MID", "SP500")):
    t0 = time.time()
    try:
        body = json.loads(handler._dispatch({"_test_scan": {"mode": mode, "universe": universe}})["body"])
        print(f"RESULT {mode} {universe} OK {json.dumps(body, ensure_ascii=False)}", flush=True)
    except Exception as e:
        ok = False
        print(f"RESULT {mode} {universe} FAILED after {time.time()-t0:.0f}s: {e}", flush=True)
    print(f"RESULT estimate calls so far: {calls}", flush=True)
sys.exit(0 if ok else 1)
