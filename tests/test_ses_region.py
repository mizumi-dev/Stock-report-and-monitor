"""
SESの本番アクセス審査がus-east-1で行き詰まったため、SES呼び出し先だけ別リージョンに
切り替えられるようにした(SES_REGION環境変数)。両Lambdaで正しくboto3.client("ses", ...)
にregion_nameが渡っていることを確認する回帰テスト。
"""
import importlib
import importlib.util
import sys
from pathlib import Path

import boto3

REGISTER_HANDLER_PATH = (
    Path(__file__).resolve().parent.parent
    / "mnt/user-data/outputs/aws_deploy/lambda_register/handler.py"
)


def _ses_call_kwargs():
    for call in boto3.client.call_args_list:
        args, kwargs = call
        if args and args[0] == "ses":
            return kwargs
    return None


def test_daily_batch_uses_default_region_when_ses_region_unset(monkeypatch):
    monkeypatch.delenv("SES_REGION", raising=False)
    boto3.client.reset_mock()
    import handler
    importlib.reload(handler)

    assert _ses_call_kwargs() == {"region_name": None}


def test_daily_batch_uses_ses_region_when_set(monkeypatch):
    monkeypatch.setenv("SES_REGION", "ap-northeast-1")
    boto3.client.reset_mock()
    import handler
    importlib.reload(handler)

    assert _ses_call_kwargs() == {"region_name": "ap-northeast-1"}

    monkeypatch.delenv("SES_REGION", raising=False)
    importlib.reload(handler)


def test_register_stock_uses_ses_region_when_set(monkeypatch):
    monkeypatch.setenv("SES_REGION", "ap-northeast-1")
    boto3.client.reset_mock()

    spec = importlib.util.spec_from_file_location("register_handler_ses_region", REGISTER_HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["register_handler_ses_region"] = module
    spec.loader.exec_module(module)

    assert _ses_call_kwargs() == {"region_name": "ap-northeast-1"}

    monkeypatch.delenv("SES_REGION", raising=False)
