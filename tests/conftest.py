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
# 実AWS接続なしでimportできるようモックする
sys.modules.setdefault("boto3", mock.MagicMock())
