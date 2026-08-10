"""
X(旧Twitter)投稿ラッパー。OAuth 1.0a user context でPOST /2/tweetsを呼ぶ。
mailer.pyのResendMailerと同じく、外部ライブラリに依存せずstdlibのみで完結させる
(daily-batchのLayerには入っているが、依存を増やさない方針を踏襲)。

投稿にはX Developer Portalで発行するAPI Key/Secret(アプリ単位)と、
投稿元アカウントのAccess Token/Secret(OAuth 1.0a、read/write権限)が要る。
値はRESEND_API_KEYと同様、AWS Lambdaの環境変数に直接設定する運用とし、
このリポジトリにはコミットしない。
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

X_API_KEY = os.environ.get("X_API_KEY", "")
X_API_SECRET = os.environ.get("X_API_SECRET", "")
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN", "")
X_ACCESS_TOKEN_SECRET = os.environ.get("X_ACCESS_TOKEN_SECRET", "")
X_POST_URL = "https://api.x.com/2/tweets"


def _oauth1_header(method: str, url: str) -> str:
    oauth_params = {
        "oauth_consumer_key": X_API_KEY,
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": X_ACCESS_TOKEN,
        "oauth_version": "1.0",
    }
    base_params = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted(oauth_params.items())
    )
    base_string = "&".join([
        method.upper(),
        urllib.parse.quote(url, safe=""),
        urllib.parse.quote(base_params, safe=""),
    ])
    signing_key = (
        f"{urllib.parse.quote(X_API_SECRET, safe='')}&"
        f"{urllib.parse.quote(X_ACCESS_TOKEN_SECRET, safe='')}"
    )
    signature = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()
    oauth_params["oauth_signature"] = signature
    return "OAuth " + ", ".join(
        f'{k}="{urllib.parse.quote(v, safe="")}"' for k, v in sorted(oauth_params.items())
    )


class XPoster:
    def post_tweet(self, text: str) -> dict:
        req = urllib.request.Request(
            X_POST_URL,
            data=json.dumps({"text": text}).encode("utf-8"),
            headers={
                "Authorization": _oauth1_header("POST", X_POST_URL),
                "Content-Type": "application/json",
                "User-Agent": "pegsignal-lambda/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"X API error {e.code}: {detail}") from e
