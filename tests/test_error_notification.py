"""
TODO#1(エラー通知)・TODO#7b(通知経路の安全なテストフック)のユニットテスト。
"""
import pytest

import handler


@pytest.fixture
def sent_emails(monkeypatch):
    sent = []
    monkeypatch.setattr(handler, "send_email", lambda subject, body, to_address: sent.append((subject, body, to_address)))
    return sent


def test_dispatch_failure_sends_notification_and_reraises(sent_emails):
    with pytest.raises(RuntimeError, match="TODO#7b"):
        handler.handler({"_test_trigger_failure": True}, None)

    assert len(sent_emails) == 1
    subject, body, to_address = sent_emails[0]
    assert "失敗" in subject
    assert "_test_trigger_failure" in body  # eventの内容が本文に含まれる
    assert "RuntimeError" in body  # スタックトレースが本文に含まれる


def test_normal_path_does_not_send_failure_notification(sent_emails, monkeypatch):
    monkeypatch.setattr(handler, "get_approved_users", lambda: [])
    handler.handler({"monitoring_only": True}, None)
    assert sent_emails == []


def test_unexpected_exception_also_triggers_notification(sent_emails, monkeypatch):
    def boom():
        raise ValueError("想定外のバグ")
    monkeypatch.setattr(handler, "get_approved_users", boom)

    with pytest.raises(ValueError):
        handler.handler({"monitoring_only": True}, None)

    assert len(sent_emails) == 1
    assert "想定外のバグ" in sent_emails[0][1]
