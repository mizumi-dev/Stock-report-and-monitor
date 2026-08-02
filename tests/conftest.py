import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SES_SENDER", "test@example.com")
os.environ.setdefault("SES_RECIPIENT", "test@example.com")
os.environ.setdefault("REGISTER_FUNCTION_URL", "https://example.com/")
os.environ.setdefault("REGISTER_TOKEN", "test-token")

# handler.pyはimport時にboto3.resource()/boto3.client()を呼ぶため、
# 実AWS接続なしでimportできるようモックする。
# boto3モジュール全体を置き換えると boto3.dynamodb.conditions.Key
# (実際のAWS接続を伴わない純粋なヘルパー)まで壊れてしまうため、
# resource/clientの2関数だけを差し替える。
import boto3
boto3.resource = mock.MagicMock()
boto3.client = mock.MagicMock()
