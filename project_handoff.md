# AWS 株式レポートサービス — プロジェクト引き継ぎ資料

## プロジェクト概要

米国株の銘柄選別（ランキング）と監視（シグナル検出）を自動化し、毎日メールレポートを送信するサーバーレスサービスをAWS上に構築中。

---

## アーキテクチャ

```
EventBridge Scheduler (平日 UTC 22:00 = JST 7:00)
    │
    ▼
Lambda: daily-batch (Python 3.12)
    ├── 週次: PEGランキング → 上位銘柄を選別レポートとしてSESで送信
    ├── 日次: watchlistテーブルの銘柄にシグナル計算 → 監視レポートをSESで送信
    └── 環境変数 REGISTER_FUNCTION_URL → register-stock のFunction URL

Lambda: register-stock (Python 3.12)
    ├── Function URL (認証なし) で公開
    ├── GETパラメータ ?ticker=NVDA で銘柄登録
    └── DynamoDB watchlist テーブルに書き込み（条件付き: 重複防止）

DynamoDB テーブル:
    - watchlist (PK: ticker) — 監視対象銘柄
    - signal_history (PK: ticker, SK: date) — シグナル履歴

SES: メール送信（us-east-1、サンドボックスモード）
```

---

## デプロイ済みリソース（us-east-1）

| リソース | 名前/ARN | 状態 |
|---------|----------|------|
| Lambda Layer | `stock-report-deps` (pandas, yfinance, boto3等) | 完了 |
| DynamoDB | `watchlist`, `signal_history` | 完了 |
| SES | 送信者・受信者アドレス verified | 完了 |
| IAM Role | `lambda-daily-batch-role` | 完了 |
| IAM Role | `lambda-register-stock-role` | 完了 |
| IAM Role | `scheduler-invoke-batch-role` | 完了 |
| Lambda | `daily-batch` | デプロイ済み・invoke成功 |
| Lambda | `register-stock` | デプロイ済み・invoke成功 |
| EventBridge Scheduler | 平日 UTC 22:00 → daily-batch 起動 | 完了 |
| Function URL | `register-stock` 用 (AuthType=NONE) | **Forbidden エラー（未解決）** |

---

## ソースファイル構成

ローカルパス: `c:\Users\motok\OneDrive\ドキュメント\Claude\Projects\株銘柄選別と監視\`

### daily-batch Lambda のソース（ルートディレクトリ）

| ファイル | 役割 |
|---------|------|
| `handler.py` | メインオーケストレータ。週次選別＋日次監視レポート実行 |
| `signals.py` | シグナル計算（MA/MACD, RSI, 出来高） |
| `data_sources.py` | NASDAQ unofficial API + yfinance でデータ取得 |
| `ranking.py` | PEGベースのランキング（成長率20%以上、PEG 3.0以下） |
| `report.py` | HTML形式メールレポート生成 |
| `deploy.sh` | デプロイ用シェルスクリプト（参考用） |

### register-stock Lambda のソース

| ファイル | 役割 |
|---------|------|
| `mnt/user-data/outputs/aws_deploy/lambda_register/handler.py` | 銘柄登録ハンドラ |

---

## 主要コード

### register-stock handler.py

```python
"""
register-stock Lambda ハンドラ
Function URL(認証なし)で直接呼ばれる想定。
例: https://xxxx.lambda-url.us-east-1.on.aws/?ticker=NVDA
"""
import os
import boto3
from datetime import datetime, timezone

WATCHLIST_TABLE = os.environ.get("WATCHLIST_TABLE", "watchlist")
dynamodb = boto3.resource("dynamodb")

SEARCH_FORM_HTML = """
<html><body style="font-family:sans-serif;max-width:400px;margin:40px auto;">
  <h3>銘柄を監視リストに登録</h3>
  <form method="GET">
    <input name="ticker" placeholder="例: NVDA" style="padding:8px;font-size:16px;">
    <button type="submit" style="padding:8px 16px;">登録</button>
  </form>
</body></html>
"""

def _response(body: str, status=200):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": body,
    }

def handler(event, context):
    params = event.get("queryStringParameters") or {}
    ticker = (params.get("ticker") or "").strip().upper()

    if not ticker:
        return _response(SEARCH_FORM_HTML)  # 検索フォーム表示

    if not ticker.isalnum() or len(ticker) > 6:
        return _response("<p>無効なティッカーです。</p>", status=400)

    table = dynamodb.Table(WATCHLIST_TABLE)
    try:
        table.put_item(
            Item={
                "ticker": ticker,
                "added_at": datetime.now(timezone.utc).isoformat(),
                "source": "email_or_search",
            },
            ConditionExpression="attribute_not_exists(ticker)",  # 重複防止
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return _response(
            f"<p>{ticker} はすでに監視リストに登録済みです。</p>"
            f"<p><a href='./'>他の銘柄を登録する</a></p>"
        )

    return _response(
        f"<p>{ticker} を監視リストに登録しました。</p>"
        f"<p><a href='./'>他の銘柄も登録する</a></p>"
    )
```

### signals.py（モメンタムシグナル部分 — 修正済み）

```python
def calc_momentum_signal(df: pd.DataFrame) -> dict:
    """RSIによる買い/売りモメンタムシグナル"""
    close = df["Close"]
    rsi = _rsi(close)
    latest_rsi = rsi.iloc[-1]
    prev_rsi = rsi.iloc[-2]

    buy = bool((latest_rsi <= 40) and (latest_rsi > prev_rsi))
    sell = bool(latest_rsi >= RSI_OVERBOUGHT)

    return {"buy_momentum": buy, "sell_momentum": sell, "rsi": round(float(latest_rsi), 1)}
```

---

## 修正済みの問題

### 1. signals.py のデッドコード削除（97行目）

- **修正前:** `buy = bool(latest_rsi <= RSI_OVERSOLD)` の直後に `buy = bool((latest_rsi <= 40) and (latest_rsi > prev_rsi))` が上書きしていた（最初の代入が到達不能）
- **修正後:** 最初の代入を削除し、2番目のロジック（RSI<=40 かつ 上昇中）のみ残した

### 2. register-stock の重複登録防止

- `ConditionExpression="attribute_not_exists(ticker)"` を追加
- 同じティッカーが既に存在する場合は「登録済み」メッセージを返す
- 日次の監視処理（daily-batch側）は別テーブル `signal_history` に書くため影響なし

---

## 解決済み: Function URL「Forbidden」(2026-08-01 解決)

### 症状

- `https://limtvgcrufuum3of3htggiovwa0vxxaj.lambda-url.us-east-1.on.aws/` にアクセスすると `{"Message":"Forbidden"}` が返る
- `aws lambda invoke` による直接実行は正常動作（200 OK）
- CloudWatch Logsを確認すると、URL経由のリクエストはLambdaに一切到達していなかった（エッジの認可層で拒否）

### 真の原因

**2025年10月のAWS仕様変更**: それ以降に作成されたFunction URLは、リソースポリシーに
`lambda:InvokeFunctionUrl` **と** `lambda:InvokeFunction` の**両方**の許可が必要になった。

- 旧来の手順（`deploy.sh`）は `lambda:InvokeFunctionUrl` しか付与していなかった
- コンソールからURL作成した場合はAWSが両方を自動付与するため、CLI作成時のみ発生する
- URL・AuthType・既存ポリシー・Organizations SCPはすべて正常だった

※ 当初推定していた「Lambda Block Public Access」は誤り。そのようなAPI/機能は
Lambdaには存在しない（`get-public-access-block-config` はS3等の機能との混同）。

### 実施した修正

```bash
aws lambda add-permission \
  --function-name register-stock \
  --action lambda:InvokeFunction \
  --principal "*" \
  --statement-id public-url-invoke-function \
  --region us-east-1
```

実行後、ブラウザからのアクセスが正常になったことを確認済み。
`deploy.sh` も両方の許可を付与するよう修正済み。

### 注意（セキュリティ）

CLIの `add-permission` では `lambda:InvokedViaFunctionUrl` 条件を付けられないため、
上記の許可は「Function URL経由」に限定されていない。より厳密にするには
コンソールからURLを削除→再作成してAWS自動生成ポリシーに置き換える
（URLが変わるので `REGISTER_FUNCTION_URL` の更新が必要）。
詳細は `aws_remediation.md` を参照。

---

## 残タスク（優先順）

1. ~~Function URL の Forbidden 解消~~ → **解決済み（上記）**
2. **daily-batch の環境変数更新** — `REGISTER_FUNCTION_URL` が削除済みの旧URL
   (`4if5x72qkq2eb4iq7qhgzgz7le0nizby...`) を指したままなのを現URLに変更
   （手順は `aws_remediation.md` 参照。メール内の登録リンクが壊れている状態）
3. **daily-batch の再デプロイ** — signals.py の修正を含めた新しいZIPをアップロード
4. **セキュリティ改善の適用** — `aws_remediation.md` の手順（予約同時実行数、
   ポリシーの厳密化、調査用IAMユーザーの削除）
5. **E2Eテスト** — Function URLから銘柄登録 → daily-batch実行 → メール受信確認

---

## IAMロール構成

### lambda-daily-batch-role

- **信頼ポリシー:** `lambda.amazonaws.com`
- **権限:**
  - DynamoDB: watchlist, signal_history の読み書き
  - SES: SendEmail
  - Lambda: InvokeFunction (register-stock)
  - CloudWatch Logs: 書き込み

### lambda-register-stock-role

- **信頼ポリシー:** `lambda.amazonaws.com`
- **権限:**
  - DynamoDB: watchlist の PutItem
  - CloudWatch Logs: 書き込み

### scheduler-invoke-batch-role

- **信頼ポリシー:** `scheduler.amazonaws.com`
- **権限:**
  - Lambda: InvokeFunction (daily-batch)

---

## Lambda設定

| 項目 | daily-batch | register-stock |
|------|------------|----------------|
| ランタイム | Python 3.12 | Python 3.12 |
| メモリ | 512 MB | 128 MB |
| タイムアウト | 300秒 | 30秒 |
| Layer | stock-report-deps | stock-report-deps |
| ハンドラ | handler.handler | handler.handler |

---

## 開発環境

- **OS:** Windows 11 Home
- **シェル:** Git Bash
- **AWS CLI:** インストール済み（バージョンが古い可能性あり）
- **リージョン:** us-east-1
- **注意:** `zip` コマンド未対応 → PowerShell `Compress-Archive` で代用
- **アカウント制約:** Lambda同時実行数上限 10

---

## コードレビュー体制

スキルファイルとして定義済み（`code-review-with-team.md`）:

- **システム設計者視点:** アーキテクチャ、セキュリティ、スケーラビリティ、障害耐性
- **アプリ開発者視点:** コード品質、バグ、パフォーマンス、テスト
- **基準:** Google Engineering Practices / SmartBear ベストプラクティス準拠
- **運用:** コーディング完了後に必ず実施、自己改善ルール付き

---

## 補足: DynamoDB条件付き書き込みについて

`ConditionExpression="attribute_not_exists(ticker)"` は「そのティッカーのレコードが存在しない場合のみ書き込む」という意味。

- **register-stock:** 新規登録時のみ使用。同じ銘柄の二重登録を防ぐ
- **daily-batch（監視処理）:** `signal_history` テーブルに日付をソートキーとして書き込むため、毎日新しいレコードが作成される。条件付き書き込みの影響は受けない
