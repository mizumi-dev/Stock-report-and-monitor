"""
メール送信ラッパー。Resend(https://resend.com)のHTTP APIを使う。

SESの本番アクセス審査がus-east-1・大阪(ap-northeast-3)どちらでも即時却下と
なり、AWSアカウント単位の自動判定が疑われる状況になったため、SESから乗り換えた。

boto3のSESクライアントと同じsend_email(Source=..., Destination=...,
Message=..., ReplyToAddresses=...)というキーワード引数のインターフェースを
持たせることで、呼び出し側(handler.py・register-stock側handler.py)の
コードは一切変更せずに済むようにしている。
"""
import json
import os
import urllib.error
import urllib.request

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_API_URL = "https://api.resend.com/emails"


class ResendMailer:
    def send_email(self, Source, Destination, Message, ReplyToAddresses=None):
        payload = {
            "from": Source,
            "to": Destination["ToAddresses"],
            "subject": Message["Subject"]["Data"],
            "html": Message["Body"]["Html"]["Data"],
        }
        if ReplyToAddresses:
            payload["reply_to"] = ReplyToAddresses

        req = urllib.request.Request(
            RESEND_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Resend API error {e.code}: {detail}") from e
