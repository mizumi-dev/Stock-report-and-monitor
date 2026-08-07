# CLAUDE.md

このファイルは、このリポジトリで作業する際にClaude Codeが常に踏まえるべき
方針・ルールをまとめたものです。

## プロジェクト概要

PegSignal — NASDAQ100銘柄をPEGレシオでスクリーニングし、トレンド/モメンタム/
出来高シグナルを監視してメールで届ける個人開発の株式情報サービス。投資助言業に
該当しないよう、完全無償・機械的な計算結果の共有・個別助言でないことを一貫して
明示する方針(詳細はTODO.mdの「金商法リスクの低減」参照)。

**対象指数の拡張予定**: 現在はNASDAQ100のみ対応。将来的にS&P500・NYダウ・
日経225への対応拡大を予定している(未着手)。新機能実装時、指数を前提にした
決め打ちコードを書く場合は、この拡張予定を踏まえて設計すること。

## セキュリティ

- **2026-08-05にAWSアクセスキーが平文でリポジトリにコミットされ、かつ
  リポジトリ自体が公開設定になっていたインシデントが発生**(詳細はTODO.md参照)。
  同種の事故を防ぐため:
  - 認証情報・APIキー・内部作業メモをコミットする前に、必ず内容を確認する
  - `.gitignore`の`*.local.md`・`.env`・`*credentials*`パターンを維持する
  - 大きめのマージ前には`security-review`スキルの使用を検討する
- **リポジトリは既定で非公開(private)とする。ユーザーから明示的な指示が
  あった場合のみ公開(public)に変更する。** 公開設定を確認・変更する際は
  必ずこの方針に従うこと。
- IAMインラインポリシーの作成・編集は、Claude自身がその権限を持っている
  場合は自分で行ってよい(ユーザーへの確認は不要)。権限が無く作成できない
  場合のみ、ユーザーに確認する。

## アーキテクチャ

- AWS Lambda 3本構成、リージョンは`us-east-1`固定
  - `register-stock`: 申込み・銘柄登録・退会・各種情報ページ・AIプロンプトページ
    などを担当(`mnt/user-data/outputs/aws_deploy/lambda_register/handler.py`)
  - `daily-batch`: 日次のスクリーニング・監視レポート生成・送信を担当
    (`handler.py`)
  - `landing-preview`: メールアドレスを登録したくない訪問者向けに、短期・中期・
    長期の選定レポートを登録不要で閲覧できる読み取り専用ページ(2026-08-06追加、
    `mnt/user-data/outputs/aws_deploy/lambda_preview/handler.py`)。
    `app_settings`のselection_snapshotを読むだけで書き込みは一切行わない。
    IAMロールもGetItemのみで書き込み権限を持たない(匿名トラフィックが最も
    集まる面を、書き込み能力を持つプロセスから意図的に隔離する設計)。
    **`preview.pegsignal.com`サブドメインとして本番ドメインへ統合済み
    (2026-08-07)**。`pegsignal.com`本体のCloudFrontディストリビューションには
    一切手を加えず、`en.pegsignal.com`と同じパターン(別ACM証明書・別
    CloudFrontディストリビューション・同じRoute53ホストゾーン内の別レコード)
    で追加した(`deploy_domain_preview.sh`)。register-stockの申込み画面には
    `PREVIEW_URL`環境変数が設定されていれば「登録せずに見る」リンクが表示される
- マルチテナント設計。`owner_token`でユーザーを識別
- DynamoDBテーブル:
  - `watchlist`: ユーザーごとの監視銘柄
  - `approved_tokens`: 申込み承認済みトークン(言語設定`lang`も保持)
  - `app_settings`: NASDAQ100構成銘柄キャッシュ、選定レポートのスナップショット等
  - `signal_history`: シグナル履歴のスナップショット(前回比較のため)
- レポートは「バッチが計算し正本を保有、ボタン押下は読み取り専用」の設計。
  個々のユーザーが手動でレポート再送信ボタンを押しても、共有スナップショット
  (`signal_history`等)は上書きされない。この境界を壊す変更をしないこと
- Lambda間で共有しているロジックファイル: `report.py`・`config.py`・
  `signals.py`・`ranking.py`・`data_sources.py`・`mailer.py`(`landing-preview`は
  `report.py`・`config.py`のみ使用)。片方だけ直して同期を忘れる事故が
  起きやすいので、共有ファイルを変更したら関係する全Lambda相当の挙動を確認する
- メール送信は`mailer.py`のResendMailer経由(Resend APIを使用)。SESの本番アクセス
  審査が繰り返し即時却下され解決不能と判断し、2026-08-06に乗り換えた。
  boto3のSESクライアントと同じ`send_email(Source=,Destination=,Message=,...)`
  インターフェースを持たせているため、呼び出し側コードは変更不要
- i18n: `report.py`の`T`辞書 + `t(key, lang)`ヘルパーで日英を管理。
  `LANG_JA`が既定

## デプロイ

- リージョン`us-east-1`固定
- 各Lambdaのzip内容・デプロイ手順は`deploy.sh`等のスクリプト参照
- デプロイ用IAMユーザーの権限には制約があり、都度必要な権限を確認しながら
  作業する(セキュリティ節のインラインポリシー方針も参照)
- **開発環境と本番環境の分離(2026-08-07)**: `develop`ブランチ=開発環境、
  `master`ブランチ=本番環境。開発環境は本番と物理的に完全に分離した別Lambda
  (`daily-batch-dev`・`register-stock-dev`・`landing-preview-dev`)・別DynamoDB
  テーブル(`watchlist-dev`等、すべて`-dev`サフィックス)で構成する
  (`deploy_dev.sh`参照)。本番のapproved_tokens・selection_snapshotを開発環境と
  共有すると、開発環境のテスト操作が本番のメール送信・正本データ更新を
  引き起こしてしまうため、テーブルレベルで分離が必須という判断
  (詳細はTODO.md参照)。**`develop`へのマージはCI/CDで開発環境へ自動デプロイ
  されるが、本番(`master`)への反映は必ずユーザーの明示的な承認(develop→master
  のPRをユーザー自身がマージすること)を経てから行う。ユーザーの指示なく
  master へのマージ・本番デプロイを行わないこと**
- `daily-batch-dev`にはEventBridgeの定期実行スケジュールを組んでいない
  (無人でのコスト発生・誤動作を避けるため、手動起動のみ)

## 開発フロー

- 機能追加は`develop`ブランチを起点にfeatureブランチを切ってPRを作成する運用
  (featureブランチ→PR→CI→developへマージ→開発環境で確認→ユーザー承認後に
  develop→masterのPRをマージして本番反映)
- 完了したタスク・既知の課題は`TODO.md`に記録する。日付・背景・対応内容・
  未着手事項を簡潔に書く運用を維持する
- **`TODO.md`には未着手・要検討の項目が残っているセクションのみを置く。
  未着手事項が無くなった(全て対応済みになった)セクションは`TODO_ARCHIVE.md`へ
  移す(内容は基本的に無編集でそのまま移動、要約や書き直しはしない)**
- ダッシュボード(Artifact)のURLがあれば、優先度の高いタスクの更新もそこに
  反映する

## テスト

- `pytest tests/`で実行。daily-batch側のテストは`pandas`/`yfinance`に依存
  しており、未導入だと構文チェックしかできない状態になる
- `.claude/hooks/session-start.sh`がセッション開始時に`requirements-dev.txt`
  を自動インストールする(Claude Code on the web限定)。masterにマージ済みの
  ため、今後のセッションでは手動インストール不要のはず
- DynamoDBのテスト用フェイク実装は`tests/fake_dynamodb.py`
