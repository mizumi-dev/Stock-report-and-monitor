"""
social.XPosterのテスト。mailer.ResendMailerのテストと同じ形で、OAuth 1.0aの
Authorizationヘッダーが正しく組み立てられ、X API v2(/2/tweets)へ正しいペイロードで
POSTされることを確認する。実際のX APIへは一切接続しない。
"""
import json
from unittest import mock

import social


def test_post_tweet_sends_correct_payload_and_oauth_header(monkeypatch):
    monkeypatch.setattr(social, "X_API_KEY", "test-key")
    monkeypatch.setattr(social, "X_API_SECRET", "test-secret")
    monkeypatch.setattr(social, "X_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(social, "X_ACCESS_TOKEN_SECRET", "test-token-secret")

    poster = social.XPoster()

    fake_response = mock.MagicMock()
    fake_response.read.return_value = b'{"data": {"id": "123"}}'
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False

    with mock.patch("social.urllib.request.urlopen", return_value=fake_response) as urlopen:
        result = poster.post_tweet("テスト投稿")

    assert result == {"data": {"id": "123"}}
    request = urlopen.call_args.args[0]
    assert request.full_url == social.X_POST_URL
    payload = json.loads(request.data.decode("utf-8"))
    assert payload == {"text": "テスト投稿"}

    auth_header = request.get_header("Authorization")
    assert auth_header.startswith("OAuth ")
    assert 'oauth_consumer_key="test-key"' in auth_header
    assert 'oauth_token="test-token"' in auth_header
    assert 'oauth_signature_method="HMAC-SHA1"' in auth_header
    assert "oauth_signature=" in auth_header


def test_post_tweet_raises_on_http_error(monkeypatch):
    import urllib.error

    monkeypatch.setattr(social, "X_API_KEY", "test-key")
    monkeypatch.setattr(social, "X_API_SECRET", "test-secret")
    monkeypatch.setattr(social, "X_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(social, "X_ACCESS_TOKEN_SECRET", "test-token-secret")

    poster = social.XPoster()
    error = urllib.error.HTTPError(
        url=social.X_POST_URL, code=401, msg="Unauthorized",
        hdrs=None, fp=mock.MagicMock(read=lambda: b'{"title": "Unauthorized"}'),
    )

    with mock.patch("social.urllib.request.urlopen", side_effect=error):
        try:
            poster.post_tweet("テスト投稿")
            assert False, "RuntimeErrorが発生するはず"
        except RuntimeError as e:
            assert "401" in str(e)
