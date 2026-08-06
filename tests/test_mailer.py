"""
mailer.ResendMailerのテスト。SESから乗り換えたResend APIラッパーが、
boto3のSESクライアントと同じsend_email(Source=..., Destination=...,
Message=..., ReplyToAddresses=...)というキーワード引数から、正しい
Resend APIリクエスト(from/to/subject/html/reply_to)を組み立てることを確認する。
"""
import json
from unittest import mock

from mailer import ResendMailer


def test_send_email_builds_correct_resend_payload():
    mailer = ResendMailer()

    fake_response = mock.MagicMock()
    fake_response.read.return_value = b'{"id": "abc123"}'
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False

    with mock.patch("mailer.urllib.request.urlopen", return_value=fake_response) as urlopen:
        result = mailer.send_email(
            Source="noreply@pegsignal.com",
            Destination={"ToAddresses": ["user@example.com"]},
            Message={
                "Subject": {"Data": "件名", "Charset": "UTF-8"},
                "Body": {"Html": {"Data": "<p>本文</p>", "Charset": "UTF-8"}},
            },
        )

    assert result == {"id": "abc123"}
    request = urlopen.call_args.args[0]
    assert request.full_url == "https://api.resend.com/emails"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload == {
        "from": "noreply@pegsignal.com",
        "to": ["user@example.com"],
        "subject": "件名",
        "html": "<p>本文</p>",
    }


def test_send_email_includes_reply_to_when_provided():
    mailer = ResendMailer()

    fake_response = mock.MagicMock()
    fake_response.read.return_value = b"{}"
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False

    with mock.patch("mailer.urllib.request.urlopen", return_value=fake_response) as urlopen:
        mailer.send_email(
            Source="noreply@pegsignal.com",
            Destination={"ToAddresses": ["admin@example.com"]},
            Message={
                "Subject": {"Data": "お問い合わせ", "Charset": "UTF-8"},
                "Body": {"Html": {"Data": "<p>内容</p>", "Charset": "UTF-8"}},
            },
            ReplyToAddresses=["reply@example.com"],
        )

    request = urlopen.call_args.args[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["reply_to"] == ["reply@example.com"]


def test_send_email_raises_runtime_error_on_http_error():
    import urllib.error

    mailer = ResendMailer()
    http_error = urllib.error.HTTPError(
        url="https://api.resend.com/emails", code=422, msg="Unprocessable",
        hdrs=None, fp=mock.MagicMock(read=lambda: b'{"message": "invalid domain"}'),
    )

    with mock.patch("mailer.urllib.request.urlopen", side_effect=http_error):
        try:
            mailer.send_email(
                Source="noreply@pegsignal.com",
                Destination={"ToAddresses": ["user@example.com"]},
                Message={
                    "Subject": {"Data": "件名", "Charset": "UTF-8"},
                    "Body": {"Html": {"Data": "<p>本文</p>", "Charset": "UTF-8"}},
                },
            )
            assert False, "RuntimeErrorが発生するはず"
        except RuntimeError as e:
            assert "422" in str(e)
            assert "invalid domain" in str(e)
