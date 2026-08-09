# CLAUDE.md

このファイルは、このリポジトリで作業する際にClaude Codeが常に踏まえるべき
方針・ルールをまとめたものです。経緯・背景は`.claude/skills/pegsignal-infra-history`
を参照してください(このファイルには現状の事実のみを簡潔に書きます)。

## プロジェクト概要

PegSignal — NASDAQ100/S&P500銘柄をPEGレシオでスクリーニングし、トレンド/
モメンタム/出来高シグナルを監視してメールで届ける個人開発の無償株式情報
サービス。投資助言業に該当しないよう、無償・機械的な計算結果の共有・
個別助言でないことを一貫して明示する方針(詳細はTODO.mdの「金商法リスクの
低減」参照)。NYダウ・日経225は未対応(低優先)。

## セキュリティ

- 認証情報・APIキー・内部作業メモをコミットする前に、必ず内容を確認する
- `.gitignore`の`*.local.md`・`.env`・`*credentials*`パターンを維持する
- 大きめのマージ前には`security-review`スキルの使用を検討する
- リポジトリは既定で非公開(private)。ユーザーから明示的な指示があった
  場合のみ公開(public)に変更する
- IAMインラインポリシーの作成・編集は、Claude自身がその権限を持っている
  場合は自分で行ってよい(確認不要)。権限が無い場合のみユーザーに確認する

## アーキテクチャ

- AWS Lambda 3本構成、リージョンは`us-east-1`固定
  - `register-stock`: 申込み・銘柄登録・退会・各種情報ページを担当
    (`mnt/user-data/outputs/aws_deploy/lambda_register/handler.py`)。
    `app.pegsignal.com`で稼働。実処理の唯一の正本
  - `daily-batch`: 日次のスクリーニング・監視レポート生成・送信を担当
    (`handler.py`)
  - `landing-preview`: 登録不要で選定レポートを閲覧できる読み取り専用ページ
    (`mnt/user-data/outputs/aws_deploy/lambda_preview/handler.py`)。
    `pegsignal.com`(apex)で稼働。`app_settings`のselection_snapshotを
    読むだけで書き込みは一切行わない(IAMロールもGetItemのみ)。申込み
    フォームを埋め込むが、`action`は`app.pegsignal.com`を向けたGETフォームで、
    実際の登録処理は複製しない
  - `preview.pegsignal.com`は無効化済み(CloudFront `EZW5FYV1PT3FO`は
    `Enabled=False`で残置、削除はしていない)
- マルチテナント設計。`owner_token`でユーザーを識別
- DynamoDBテーブル:
  - `watchlist`: ユーザーごとの監視銘柄
  - `approved_tokens`: 申込み承認済みトークン(言語設定`lang`も保持)
  - `app_settings`: 対象指数の構成銘柄キャッシュ、選定レポートのスナップショット等
  - `signal_history`: シグナル履歴のスナップショット(前回比較のため)
- レポートは「バッチが計算し正本を保有、ボタン押下は読み取り専用」の設計。
  この境界を壊す変更をしないこと
- Lambda間で共有しているロジックファイル: `report.py`・`config.py`・
  `signals.py`・`ranking.py`・`data_sources.py`・`mailer.py`(`landing-preview`は
  `report.py`・`config.py`のみ使用)。共有ファイルを変更したら関係する
  全Lambda相当の挙動を確認する
- メール送信は`mailer.py`のResendMailer経由(Resend API)
- i18n: `report.py`の`T`辞書 + `t(key, lang)`ヘルパーで日英を管理。
  `LANG_JA`が既定
- 対象指数(NASDAQ100/S&P500)は`config.py`の`UNIVERSES`辞書で管理

## デプロイ

- リージョン`us-east-1`固定
- 各Lambdaのzip内容・デプロイ手順は`deploy.sh`等のスクリプト参照
- デプロイ用IAMユーザーの権限には制約があり、都度必要な権限を確認しながら
  作業する
- `develop`ブランチ=開発環境、`master`ブランチ=本番環境。開発環境は本番と
  完全に分離した別Lambda(`-dev`サフィックス)・別DynamoDBテーブルで構成する
  (`deploy_dev.sh`参照)。**`develop`へのマージはCI/CDで開発環境へ自動デプロイ
  されるが、本番(`master`)への反映は必ずユーザーの明示的な承認を経てから
  行う。ユーザーの指示なくmasterへのマージ・本番デプロイを行わないこと**。
  既定は「develop→masterのPRをユーザー自身がマージすること」だが、
  (2026-08-08追記)**ユーザーがその場でClaudeに対しmasterへのマージを
  明示的に依頼した場合は、Claude自身がマージしてよい**。この場合も
  develop環境でのテストパス・動作確認が済んでいることが前提
- `daily-batch-dev`にはEventBridgeの定期実行スケジュールを組んでいない
  (手動起動のみ)

## 開発フロー

- 機能追加は`develop`ブランチを起点にfeatureブランチを切ってPRを作成する運用
  (featureブランチ→PR→CI→developへマージ→開発環境で確認→ユーザー承認後に
  develop→masterのPRをマージして本番反映)
- 完了したタスク・既知の課題は`TODO.md`に記録する。日付・背景・対応内容・
  未着手事項を簡潔に書く運用を維持する
- `TODO.md`には未着手・要検討の項目が残っているセクションのみを置く。
  未着手事項が無くなったセクションは`TODO_ARCHIVE.md`へ移す(無編集で移動)
- ダッシュボード(Artifact)のURLがあれば、優先度の高いタスクの更新もそこに
  反映する
- **note/X等の外部公開文言(マーケティングコピー)は、公開前に必ず実装
  (該当するコード)と突き合わせて事実確認する**(2026-08-09追記)。集客展開
  作業で「低PEG銘柄」→実際は成長率ゲートもある「低PEG成長株」、「設定した
  頻度で配信」→実際は期間モード選択で決まる固定頻度、「定期配信」→登録
  無しでは配信されない、といった不正確な表現を複数回、コード側の実装
  (`ranking.py`の`passes_gate()`・`handler.py`の`should_send_selection()`等)
  との突き合わせで発見・修正した。人間が書いた文章でも「サービスが実際に
  何をするか」の記述は推測で済ませず、該当コードを読んで確認する
- **CloudFront/インフラ関連の変更を本番反映する前に、developに他セッションが
  加えた変更との相互作用が無いか確認する**(2026-08-09追記)。canonical URLの
  修正が、別セッションが追加した地理判定機能(`CloudFront-Viewer-Country`)と
  組み合わさることで、Googlebot等(大半が米国IPからアクセス)が日本語向けの
  URLを英語版と誤認識する不具合を新たに生む寸前だった。実際にヘッダーを
  変えてhandler.pyを直接動かし、挙動を再現して確認したうえで気づけた
  (推測ではなく実行して確認する)

## テスト

- `pytest tests/`で実行。daily-batch側のテストは`pandas`/`yfinance`に依存する
- `.claude/hooks/session-start.sh`がセッション開始時に`requirements-dev.txt`
  を自動インストールする(Claude Code on the web限定)
- DynamoDBのテスト用フェイク実装は`tests/fake_dynamodb.py`
