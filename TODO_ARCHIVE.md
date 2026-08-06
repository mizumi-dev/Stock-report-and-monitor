# 完了タスクのアーカイブ

`TODO.md`から、対応が完了した(未着手・要検討の項目が残っていない)セクションを
移した記録。日付は元の記載のまま、内容は基本的に無編集(移動のみ)。

最終更新: 2026-08-06

## CI/CD自動デプロイパイプラインの構築（2026-08-06、完了）

`deploy.sh`は整備済みだが実行はAWS CLIを手で叩く運用で、masterマージ後の
反映漏れが起きやすかった(TODO#12として記録済みの残件)。

- GitHub ActionsからAWSへの長期アクセスキーを一切持たせず、OIDC連携で
  masterブランチからのみ一時的にAssumeRoleできる構成にした。
  - IAM OIDCプロバイダー(`token.actions.githubusercontent.com`)を新規作成
  - `github-actions-deploy-role`ロールを作成
  - インラインポリシーで`lambda:UpdateFunctionCode`・`lambda:GetFunction`のみを
    `daily-batch`・`register-stock`の2関数ARNに限定して付与(それ以外のAWS操作は
    一切できない最小権限)
  - `.github/workflows/deploy.yml`を新規作成。既存の`tests.yml`がmaster上で
    成功した直後にのみ`workflow_run`で発火し、`deploy.sh`と同じzip構成で
    両Lambdaのコードを更新する
- PR #21マージ後の初回自動デプロイが
  `Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity`
  で失敗した件。CloudTrailで実際のOIDCトークンの`sub`クレームを確認したところ、
  想定していた`repo:OWNER/REPO:ref:...`ではなく、アカウントID・リポジトリIDが
  付与された`repo:mizumi-dev@199311410/Stock-report-and-monitor@1319011790:ref:refs/heads/master`
  という非標準の形式だった(この環境固有の挙動)。信頼ポリシーの`sub`条件を
  実際の値に合わせて修正し解消。以降のPR(#25・#26・#27・#28)のマージで
  自動デプロイが正常動作することを確認済み
- 検証用に一時的に`claude-deploy`ユーザーへアタッチしていた`IAMFullAccess`
  (AWS管理ポリシー)は、ユーザーがデタッチ済み(2026-08-06)
- **対象外(意図的)**: DynamoDBテーブル作成・IAMロール作成・Lambda Layer公開・
  Function URL設定などの初回セットアップ手順は`deploy.sh`のまま。CI/CDが
  自動化するのはコード更新のみで、インフラ変更は引き続き手動で意図的に行う

## メール配信をSESからResendへ乗り換え（2026-08-06、完了）

SESの本番アクセス審査が、us-east-1(2回却下)・大阪ap-northeast-3(正しい情報で
申請したが数秒で即時DENIED)のどちらでも通らず、**AWSアカウント単位で何らかの
自動判定フラグが立っている可能性が高い**と判断。「外部依存を増やしたくない」
という以前の方針(下記【最優先】SES本番アクセス再却下への対応、参照)を見直し、
Resend(https://resend.com)への乗り換えを決定・完了した。

- 共有モジュール`mailer.py`を新設。boto3のSESクライアントと同じ
  `send_email(Source=,Destination=,Message=,ReplyToAddresses=)`という
  キーワード引数インターフェースを持つ`ResendMailer`を実装し、`daily-batch`・
  `register-stock`両方の呼び出し箇所(計5箇所)は無変更で乗り換え完了
  (`ses = ResendMailer()`に差し替えるだけで済んだ)
- `deploy.sh`・`.github/workflows/deploy.yml`の両方で`mailer.py`をzipに
  含めるよう修正。`SES_REGION`(乗り換え検討時に追加したものの結局不要になった
  環境変数)を削除し`RESEND_API_KEY`に置き換え。Lambda実行ロールの
  `ses:SendEmail`権限も不要になったため削除
- 実地テスト(`_test_trigger_failure`ペイロードでdaily-batchを起動)で、
  Resend側のCloudflareボット対策によりPython urllibの既定User-Agentが
  ブロックされる問題(HTTP 403, error code 1010)を発見・修正(明示的な
  `User-Agent`ヘッダーを付与、PR #26)
- Resendアカウント作成・`pegsignal.com`ドメインのDKIM(TXT)・SPF/送信用(MX・TXT)
  レコードをRoute53に追加してドメイン検証完了、`RESEND_API_KEY`を両Lambdaの
  環境変数にAWSコンソールから直接設定、実地確認(失敗通知メールが実際に届く
  ことを確認)まで全て完了
- テスト追加(`tests/test_mailer.py`)、pytest全件pass
- 大阪リージョンで進めていたSESの申請(ケースID: 178602306000835)は保留のまま
  でよいと判断(Resendへの乗り換えが完了したため、SES側の結果を待つ必要性が
  無くなった)

## 【最優先】SES本番アクセス再却下への対応（2026-08-04、Resendへの乗り換えで解消）

2回目の却下(具体的理由なしの定型文)を受けて対応を続けたが、`WebsiteURL`修正
(却下後はAPIロックされ不可、AWSサポートへの人力修正依頼も未解決)・SPF/DMARC
レコード追加・Service Quotas経由の別ルート試行・リージョン変更(大阪)による
再申請、いずれも解決に至らず、最終的にはAWSアカウント単位の自動判定フラグが
原因と判断。上記の通りResendへの乗り換えで解決した。

- `sesv2 put-account-details`直接修正は却下後`ConflictException`でロック
- `pegsignal.com`にSPF・DMARCレコードを追加(SPF: `v=spf1 include:amazonses.com ~all`、
  DMARC: `v=DMARC1; p=none; rua=mailto:amsllr759@gmail.com`)
- AWS Service Quotasから「SES Daily Sending Quota」引き上げを直接リクエストする
  代替ルートも試したが、結果は`CASE_CLOSED`(未承認)
- 大阪(ap-northeast-3、履歴のないクリーンなリージョン)で正しい情報を用いて
  再申請したが、数秒で即時DENIED(ケースID: 178602306000835、保留中)

## en.pegsignal.comインフラの実デプロイ（2026-08-06）

英語版サイトの残件だった、コード用意済み・未実行のインフラスクリプト2本を実行した。

- **対応済み**: `deploy_domain_en.sh`を実行。`en.pegsignal.com`用のACM証明書
  (DNS検証、us-east-1)・CloudFrontディストリビューション(オリジンに
  `X-Site-Lang: en`ヘッダーを常時付与)・Route53 ALIASレコードを新規作成
- **対応済み**: `deploy_patch_ja_lang_header.sh`を実行。既存の`pegsignal.com`側
  ディストリビューションのオリジンにも対になる`X-Site-Lang: ja`ヘッダーを追加
- **IAM運用メモ**: 実行にはACM・CloudFront・Route53の権限が新たに必要だった。
  `claude-deploy`ユーザーの既存インラインポリシー合計が2048バイト上限に近く、
  追加の余地を作るため`dynamodb-multi-tenant-migration`(Query権限のみ)を
  `deploy-scoped`ポリシーに統合してから、新規`en-domain-deploy`ポリシー
  (ACM/CloudFront/Route53、必要最小限のアクションのみ)を追加
- **実地確認済み**: `https://en.pegsignal.com/`にアクセスし、英語で表示されることを
  確認(現在は`SIGNUPS_ENABLED=false`のため新規登録一時停止画面が表示されるが、
  その画面自体が英語で出ることを確認できたため、`X-Site-Lang`ヘッダーによる
  言語判定が正しく機能していることも合わせて確認できた)
- この開発環境からは外部ネットワークへのアクセスが制限されており、動作確認は
  ユーザーの実機ブラウザで実施した

## 開発体験・運用ルールの整備（2026-08-06）

セキュリティインシデントを受けて「最新の知見をどう運用に落とし込むか」を検討した流れで、
プロジェクト運用ルールを口頭確認のたびに再説明しなくて済むよう明文化。

- **対応済み**: `CLAUDE.md`を新設。プロジェクト概要・対象指数拡張予定
  (NASDAQ100の次はS&P500・NYダウ・日経225を予定)・セキュリティ方針
  (リポジトリは既定で非公開、ユーザー指示時のみ公開／IAMインラインポリシーは
  権限があれば自分で作成してよい)・アーキテクチャ・デプロイ・開発フロー・
  テストの各節を記載。内容は事前にユーザーレビュー済み
- **対応済み**: セッション開始時に`requirements-dev.txt`(yfinance/pandas/pytest/boto3)を
  自動インストールする`.claude/hooks/session-start.sh`を追加、`.claude/settings.json`に
  登録(Claude Code on the web限定、同期モード)。daily-batch側のテストがpandas/yfinance
  依存で、手動`pip install`を忘れると構文チェックしかできない状態になっていた問題への対応。
  PR #19で提出・マージ済み
- **試行→撤回**: ダッシュボード(Artifact)を「`TODO.md`を正本として機械的に複製する」
  方式に一度変更したが、ロードマップ・PR一覧・完了/バックログをキュレーションした
  従来の見やすい形式に戻したいとの判断で撤回。ダッシュボードは引き続きClaudeが
  適宜要約して更新する運用のまま(`CLAUDE.md`の記載も元に戻した)

## UI改善: 選定レポートの見出し・件名をモード別に出し分け（2026-08-06）

- **対応済み**: 選定レポートの見出し・メール件名が短期/中期/長期どのモードでも
  「今週のPEGスクリーニング結果」固定だったのを、SHORT→「本日の」、MID→「今週の」、
  LONG→「今月の」PEGスクリーニング結果、と判定期間モードに応じて出し分けるよう修正
  (`report.py`の`selection_subject(mode, lang)`)
- あわせて`handler.py`内で2箇所ハードコードされていた件名文言を`selection_subject()`に
  一本化し重複を解消。テスト4件追加、全140件pass。PR #20で提出・マージ済み

## UI改善・新機能（2026-08-05）

- **対応済み**: 監視レポートの●/○(黒丸・白丸)表示を、点灯有無が分かりにくいとの
  指摘を受けて色付きの丸(買い行🟢・売り行🔴、消灯時⚪)に変更(`_signal_dot`)
- **対応済み**: レポートを見て気になった銘柄をAIに分析させたい利用者向けに、
  「🤖 AIに分析してもらう →」リンクを選定・監視両レポートの各銘柄に追加。
  メール本文はJavaScriptが使えない(Gmail等がscriptタグを除去)ため、遷移先の
  register-stockに新ページ(`?ai_prompt=1&ticker=XXXX`、トークン不要)を作り、
  そこにコピー用プロンプトの表示・クリップボードコピーボタン(`navigator.clipboard`)
  を実装。プロンプトは「客観的な分析」を依頼する内容にとどめ、売買推奨を求める
  文言は避けている(投資助言に該当しないための方針と一貫させるため)
- テスト13件追加、全137件pass

## レポート即時発行ボタンの低コスト化（2026-08-04）

登録者が増えると「レポートを今すぐ送る」ボタンの押下も増え、その都度yfinanceから
再計算していると外部API負荷・実行コストが利用者数に比例して増えてしまう懸念への対応。

**方針**: シグナルは日足ベースで計算しており、日足が1本確定するまで値は変わらない。
そのため「配信は常にバッチ実行時点のスナップショットのみ。ボタン押下は新規計算を
トリガーしない」という設計にした（ボタンを毎日押す利用者がいても、シグナル履歴という
利用者間で共有するキャッシュが本人の押下タイミングですり替わることもない）。

- **監視レポート**: 元々毎日全利用者分バッチ実行されるため、`signal_history`に
  price・prev_close・peg・prev_signalsも一緒に保存するよう拡張(`save_signal_snapshot`)。
  ボタン押下時(`send_monitoring_report_now`)は当日分(無ければ前日分)を読むだけで、
  新規のyfinance取得はゼロ。登録直後でまだ誰の分も計算されていない銘柄のみ、その場で
  1回取得して保存する
- **選定レポート**: モードごとのランキング+シグナルを`app_settings`にスナップショット
  保存(`save_selection_snapshot`/`get_selection_snapshot`、キーは`config.selection_snapshot_key`)。
  ボタン押下時(`send_selection_report_now`)は保存済みスナップショットを読んで送るだけ。
  そのモードで一度もバッチが走っていない場合のみ、その場で1回スキャンする
  (中期・長期モードは配信頻度が低いため、稀にこのケースが発生する)
- **副次効果**: `run_monitoring_reports`(定時実行)を利用者ごとのループから
  「モードごとに監視銘柄の和集合を作り、同じ銘柄は1回だけ取得・計算」する構成に変更。
  複数人が同じ銘柄を登録していた場合の重複取得を解消(ボタン機能とは無関係に、定時実行
  自体のコストも削減)
- レポート本文には「◯月◯日時点のデータです」の注記を追加(`_as_of_note`、ボタン押下で
  古いスナップショットが送られた場合に利用者が気づけるように)
- テスト9件追加(`test_multi_tenant.py`)。pandas/yfinanceをこの開発環境に一時インストールし
  実行して確認(既存分含め全114件pass。通常この環境ではpandas依存のテストは実行できず
  構文確認のみだったが、今回は変更規模が大きいため実行確認した)

## 英語版追加後のリファクタリング（2026-08-04）

英語版対応で`report.py`・`mnt/user-data/outputs/aws_deploy/lambda_register/handler.py`が
複雑化したとの指摘を受け、`/simplify`スキルで4観点(再利用性・簡素化・効率性・抽象度)の
並行レビューを実施し、見つかった問題を対応した。

- **重複していた期間モードの英語ラベルを一本化**: `report.py`の`_MODE_LABEL_EN`と
  register-stock側の`_PERIOD_PRESET_LABELS_EN`が同じ3モードの英語ラベルを別々に
  持っていた。`config.py`の`PERIOD_PRESETS`各エントリに`label_en`・`description_en`を
  追加し、両ファイルとも単一の情報源(config.py)を参照するように変更、ローカルの
  重複辞書を削除
- **`_CYCLE_LABEL`/`_SEND_FREQ_LABEL`をja/en一本化**: register-stock側で日本語版と
  英語版が別々の辞書だったものを`{key: {"ja":..., "en":...}}`の単一辞書にまとめた
- **register-stock Lambdaの冗長なDynamoDB読み取りを削減**: `handler()`が既に取得済みの
  `token_item`を、`_is_valid_token()`(トークン再取得→削除しインライン判定に変更)・
  `_set_user_lang()`・`_handle_confirm()`が個別に再取得していた。authしたリクエスト
  1件につきDynamoDB読み取りが最大3回→1回に削減(Function URL経由のレイテンシ改善)
- **`report.py`に文言の出し分けを集約する`T`辞書 + `t(key, lang)`ヘルパーを追加**:
  見出し・ラベル・凡例・警告文言など、語順が言語間で変わらない単純な文言は全て
  `T`辞書経由に統一。カード本文のように語順自体が言語で大きく異なる箇所
  (銘柄カード・監視ブロック)は無理にテンプレート共通化せずja/en個別のままとした
  (共通化すると条件分岐が増えてかえって読みにくくなるため、過剰な抽象化は避けた)
- テスト(`test_report.py`・`test_register_handler.py`、計80件)は無修正のまま全件pass
  (出力される文言・HTMLは1文字も変えていないことの裏付け)。`report.py`は差分で
  正味-53行

## 英語版: レポート本文の英訳・米国向け証券リンク（2026-08-04）

英語版の残件のうち、daily-batchが生成するレポート本文(`report.py`)自体の英訳と、
英語圏利用者向けの証券リンク出し分けに対応。

- **証券リンクの選定**: Web検索で米国口座数ランキングを調査（Fidelity約5,200万
  > Schwab約3,780万 ≈ Vanguard約5,000万(投信中心) > Robinhood約2,740万）。
  ただしログイン不要で個別銘柄ページへ直接リンクできることを確認できたのは
  **Fidelity**(`digital.fidelity.com/prgw/digital/research/quote/dashboard/summary?symbol=`、
  1位)と**Robinhood**(`robinhood.com/stocks/`、4位・SEO公開ページ)のみ。
  Schwab(2位)は要ログインの可能性が高く、Vanguard(3位)は投信中心で個別株
  ページの公開URLパターンが確認できなかったため見送った。ユーザーの了承の上、
  3位・4位でリンク可能な方を採用する方針とした
- **`report.py`の全面英語対応**: `build_selection_report`/`build_monitoring_report`に
  `lang: str = "ja"`引数を追加（デフォルトは日本語のまま、既存呼び出しは無変更で動く）。
  シグナル凡例・トレンド/モメンタム文言・サマリー文・見出し・免責事項・退会リンク・
  登録リンクなど全テキストをlang分岐。`_broker_links(ticker, lang)`もja/enで
  出し分け（ja→moomoo証券・楽天証券、en→Fidelity・Robinhood）
- **期間モードラベル**: 引数名を`mode_label`(整形済み文字列)から`mode`(生のモード
  キー、例:"SHORT")に変更し、`report.py`内でja/en両方のラベルを解決するように変更
  （config.pyのPERIOD_PRESETSはja版の単一情報源として引き続き参照、en版のみ
  ローカル対訳表`_MODE_LABEL_EN`を追加。register-stock側の`_PERIOD_PRESET_LABELS_EN`
  と同じ発想で、共有のconfig.pyには手を入れない）
- **daily-batch側の連携**: `get_approved_users()`が返す辞書に`"lang"`を追加
  (`approved_tokens`に保存済みの値、未設定時は"ja")。`run_selection_reports`/
  `run_monitoring_reports`で各利用者の`lang`を`build_*_report()`に渡し、
  メール件名も出し分け
- テスト6件追加(`test_report.py`)、`test_multi_tenant.py`の`fake_build`が新しい
  `lang`引数を受け取れるよう修正（pandas依存のためこの開発環境では実行できないが、
  シグネチャ変更に追従が必要だったため対応）
- **en.pegsignal.comのインフラ実デプロイ**: 当初の残件だったが、2026-08-06に実施済み
  (詳細は上部「en.pegsignal.comインフラの実デプロイ」参照)

## 実利用フィードバック対応 第2弾: 英語版（2026-08-03、着手）

`en.pegsignal.com`サブドメイン方式で英語版を実装。

- **インフラ**: `deploy_domain_en.sh`を新規作成（`deploy_domain.sh`と同じ冪等パターンで
  ACM証明書・CloudFront・Route53を用意。`pegsignal.com`側の証明書・ディストリビューションには
  一切手を加えない独立構成）。CloudFrontのオリジンカスタムヘッダーで`en.pegsignal.com`には
  `X-Site-Lang: en`を常時付与し、register-stock Lambdaがこのヘッダーの有無で言語を判定する。
  対になる`X-Site-Lang: ja`を既存の`pegsignal.com`ディストリビューションにも付与するための
  パッチスクリプト`deploy_patch_ja_lang_header.sh`も作成。実行は2026-08-06に完了
- **言語の永続化**: 申込み時にアクセス元ドメイン(ヘッダー)から判定した言語を
  `approved_tokens`の`lang`属性として保存。以降はどちらのドメインからアクセスしても
  本人の設定が優先される。メイン画面から`?lang=en`/`?lang=ja`でいつでも手動切替可能
  （期間モード切替と同じ仕組み）
- **翻訳済み**: 申込みフォーム、メイン画面（監視リスト登録・期間モード変更・レポート即時送信・
  退会）、関連する通知メッセージ一式
- **フッター・期間モード表示の英訳 対応済み（2026-08-03）**: 実地確認で「フッターと
  Time horizonのプルダウンが日本語のまま」との指摘を受けて修正。`_page()`のフッターを
  `lang`引数で出し分け（遷移先ページ自体はまだ日本語のみである旨を明記）。
  `config.py`の`PERIOD_PRESETS`(daily-batchと共有)には手を入れず、register-stock側に
  ローカルな英語ラベル対訳表(`_PERIOD_PRESET_LABELS_EN`)を追加する形で対応し、
  共有データへの影響を避けた
- **別件: 監視レポートに登録ページへのリンクを追加 対応済み（2026-08-03）**:
  選定レポートには銘柄登録ページへのリンクがあったが、監視レポートにはなかったため追加。
  `build_monitoring_report`に`register_base_url`/`register_token`を渡せるようにし、
  daily-batch側から渡すよう変更
- **ユーザーによる実地確認完了（2026-08-03）**: `register-stock`をデプロイし、
  `?lang=en`での英語表示・フッター・期間モードドロップダウンの英語化を確認済み
- **PR #8として提出・マージ済み**: ここまでの内容(コア画面の英訳・言語切替基盤・
  監視レポートのリンク追加)を一区切りとしてPR化
- テスト16件追加、既存分と合わせて58件全てpass

### 英語ページ翻訳の続き（2026-08-03、着手）

上記「未翻訳」のうち、静的ページ4種+説明ページ+確認メール類を対応。

- **対応済み**: `?about=1`・`?contact=1`・`?privacy=1`・`?terms=1`・`?info=1`の全ページに
  `lang`引数を追加し英訳版を実装。`_render_info()`は`config.py`の実際の設定値から表を
  動的生成する構造だったため、英語ラベル対訳表(`_CYCLE_LABEL_EN`・`_SEND_FREQ_LABEL_EN`)を
  追加してJA版と同じロジックを再利用
- **対応済み**: `_handle_contact()`のバリデーション・完了メッセージを`lang`で出し分け。
  問い合わせメール本文にも言語設定を記載（サポート時の参考情報として）
- **対応済み**: `_send_confirmation_email()`・`_send_confirmation_email_reminder()`に
  `lang`引数を追加し件名・本文を英訳。再登録時のリマインダーメールは、その場のリクエスト言語
  ではなく**登録済み利用者本人の保存済み言語設定**(`_get_user_lang(existing)`)を使うようにした
  （別ドメインから再登録を試みても、本人が選んだ言語でメールが届くようにするため）
- **対応済み**: 各所に残っていた「(Japanese only for now)」という古い注記を、実際に翻訳が
  完了したページ(info/privacy/terms)へのリンクから削除
- テスト13件追加（`TestInfoPagesLanguage`・`TestSignup`拡張）、全75件pass
- **残っていた「daily-batchのレポート本文自体の英訳・米国向け証券リンク・
  en.pegsignal.comインフラ実デプロイ」は、いずれもその後のセクションで対応済み**

## メールからも退会できるようにする（2026-08-03、実利用フィードバック対応 第3弾より）

これまで退会（登録データの自己削除）は登録ページ(メイン画面)からのみ可能で、
レポートメール自体には導線がなかった。選定・監視の両レポート末尾に
「配信停止・退会はこちら →」リンクを追加（`?token=...&delete_account=1`で
確認画面を表示、確認画面のボタンをもう一度押すまで実際の削除は実行されない
既存の二段階確認フローをそのまま利用）。`report.py`にテスト3件追加、
全64件pass。`daily-batch`Lambdaへデプロイ済み。

## 選定レポートの内容改善（2026-08-03、配信タイミング再設計・証券リンク）

- **配信タイミングの再設計**: 従来は`cron(0 22 ? * MON-FRI *)`というUTC固定時刻での
  実行だったため、夏時間(DST)未対応かつ「UTC月曜に発火する処理が日本時間では火曜朝に
  届く」というズレがあった。「米国市場が開く前に、直前の取引日までのデータで届けたい」
  という要望を踏まえ、EventBridge Schedulerの時刻指定を`America/New_York`のローカル
  時刻基準(`cron(0 6 ? * MON-FRI *)`、`--schedule-expression-timezone`指定)に変更。
  AWSがDSTを自動調整するため今後手動対応は不要。週末は新しい取引データが存在しない
  ため、月曜朝6時ET実行時点で自動的に前週金曜までのデータになる。`deploy.sh`修正済み。
  **本番のEventBridge Scheduler(`daily-stock-report`)への反映も完了（2026-08-03、
  ユーザーがAWSコンソールから直接編集）**。当初us-east-1のスケジュールがコンソール上
  見当たらなかったが、リージョン選択が東京(ap-northeast-1)になっていたことが原因と判明
- **証券会社への行動リンクを追加**: 選定・監視レポートの各銘柄カードに、moomoo証券
  (`https://www.moomoo.com/ja/stock/{ticker}-US`、ユーザー本人が実機で確認した実在URL)・
  楽天証券(`https://www.rakuten-sec.co.jp/web/market/search/us_search/quote.html?ric={ticker}.O`、
  RICコード形式、実在URLを検索で確認)へのリンクを追加。SBI証券はログイン必須のため
  見送り。現時点では利用者は全員日本語版想定のため日本の証券会社のみ。英語版実装時に
  利用者ごとの言語属性を持たせ、英語圏の利用者には米国の証券会社(要調査)に出し分ける
  予定。`report.py`にテスト2件追加
- TODO#13・#14 対応済み（詳細はアーカイブ内の各項目を参照）

## 外部公開に向けた進行中の作業（2026-08-02）

「誰でも申し込める」形での外部公開を進めた。

- **SES本番アクセス申請: 却下済み（2026-08-02判明）**。`aws sesv2 get-account`で確認したところ
  `ProductionAccessEnabled: false`、`ReviewDetails.Status: DENIED`。当初`CaseId: 177263262900237`
  (`WebsiteURL`が`aws-mocha1-first-try.com`という過去の別サービス用仮ドメイン)と記録していたが、
  実際にAWS上で確認したところ直近の却下は`CaseId: 178564814600583`
  (`WebsiteURL`がGitHubリポジトリのURL)で、記録と実態がずれていた。
  サービスにふさわしい新ドメイン`pegsignal.com`をRoute53で取得完了。
  プライバシーポリシー/利用規約への導線を含むサイトを用意。
  (その後の展開は`TODO.md`本体の「【最優先】SES本番アクセス再却下への対応」参照)
- **SESの送信元/宛先アドレスを個人Gmailから分離（2026-08-02）**: `pegsignal.com`を
  SESでドメイン認証(TXT+DKIM×3、Route53に投入)し、`SES_SENDER`を個人Gmailから
  `noreply@pegsignal.com`に変更。`SES_RECIPIENT`(daily-batchのエラー通知等の宛先)は
  事業用アドレス`amsllr759@gmail.com`に変更(SESサンドボックス中のため事前にメール検証済み)。
  `daily-batch`・`register-stock`両Lambdaの環境変数に反映し、`LastUpdateStatus: Successful`
  を確認済み

### 利用者の不安解消（2026-08-02）

装飾ゼロ・運営者情報なし・問い合わせ手段なしの状態は「怪しいサイト」に見えるとの指摘を受け、
register-stock(`mnt/user-data/outputs/aws_deploy/lambda_register/handler.py`)に以下を追加:

- [x] **見た目の装飾**: `<style>`ブロックを追加し、カード型レイアウト・ブランドカラー
  (`#16a34a`系グリーン)・フォーム部品のスタイリングを実装。外部CSS/フォントは使わず
  Lambda単体で完結する設計を維持
- [x] **運営者情報ページ(`?about=1`)**: 個人開発である旨、サービス概要、
  メールアドレスの取り扱い方針を明記。全ページ共通フッターからリンク
- [x] **お問い合わせフォーム(`?contact=1`)**: 簡易CAPTCHA付きのフォームから
  `SES_RECIPIENT`(`amsllr759@gmail.com`)宛にメール送信(`ReplyToAddresses`に
  返信用アドレスを設定)。register-stockの環境変数に`SES_RECIPIENT`を追加
  (`deploy.sh`・本番Lambda両方に反映)
- [x] **メールアドレスの用途説明**: 申込みフォームに「レポート配信・ログイン用
  リンクの送信にのみ使用し、第三者提供は行わない」旨の一文を追加
- [x] **流出リスクの透明性**: プライバシーポリシーに、DynamoDB保管・招待トークンに
  よるアクセス制御・個人開発のため大規模なセキュリティ監査は未実施である旨を明記
- [x] **登録解除（退会）機能** — アクセス用リンクを持つ利用者が、確認画面
  (`?token=...&delete_account=1`)→実行(`?token=...&delete_account_confirm=1`)の
  2ステップで、メールアドレス・監視リスト・期間モード設定を即時に完全自己削除できる
  セルフサーブ機能として実装(お問い合わせフォームは経由しない)
- `docs/privacy-policy.md`・`docs/terms-of-service.md`の連絡先記載も
  個人メールアドレス直書きから`?contact=1`への案内に更新
- テスト10件追加(`tests/test_register_handler.py`、about/contact/退会関連)、
  既存分と合わせて32件全てpass
- 実AWS環境へのコードデプロイ完了（2026-08-02）
- **ブラウザでの実地確認完了（2026-08-02）**
- **退会機能のバグを本番で発見・修正（2026-08-02）**: 実際に退会操作を行ったところ
  内部サーバーエラーが発生。原因は`stock-report-register-role`に`approved_tokens`・
  `app_settings`テーブルへの`DeleteItem`権限が付与されていなかったこと。
  `iam:PutRolePolicy`を限定して追加発行してもらい解消(`deploy.sh`にも反映済み)。
  詳細は`docs/architecture.md`4節※3を参照。ユーザーが実際に退会操作を再実行し、
  正常動作を確認済み
- **UI/UX改善（2026-08-02）**: `expert-panel-review`で3案を比較し**段階的開示**を採用。
  3ステップの visual summary、`<details>`での詳細折りたたみ、`?info=1`の`<details>`化。
  テスト32件は文言変更後も全てpass
- **UI/UX改善 第2ラウンド（2026-08-02）**: 成長/コンバージョンデザイナーを加えたパネルで
  **サンプル同梱+具体的信頼文言**を採用。具体的便益を見出しに、信頼文言を配置、
  配信メールのサンプルをヒーロー領域に追加。テスト32件全てpass
- **SES本番アクセスの再申請 提出済み（2026-08-02）**: 却下済みケースへの返信という形で、
  新ドメイン・ダブルオプトイン方式・自己退会機能・前回却下理由の解消状況を記載して再申請
  (その後の展開は`TODO.md`本体の「【最優先】SES本番アクセス再却下への対応」参照)
- **選定レポートメールのリンク切れバグを発見・修正（2026-08-02）**: `report.py`の
  銘柄別リンクの`href`属性で`&`をエスケープせず生のまま埋め込んでいたことが原因
  (Gmailモバイルアプリのサニタイザがクエリ文字列を誤解釈)。回帰防止テストを追加
  - IAM運用メモ: `pegsignal-deploy-temp`ユーザーのインラインポリシー合計サイズが
    上限(2048バイト)に達した際は、使い終わった一時ポリシーを削除して空きを作った。
    「使い終わったら消す」運用を徹底する
- **「今すぐ送信」ボタンのマクロ連打対策を追加（2026-08-02）**: 利用者ごとに直近の
  リクエスト時刻を`app_settings`に記録し、300秒以内の再リクエストを拒否する
  クールダウンを追加。テスト5件追加
- マルチテナント化（TODO#6a）は対応済み
- **マネタイズ・国際展開方針を決定（2026-08-02）**: 決済機能は持たず、広告
  （非パーソナライズ・Web画面限定）またはアフィリエイトで検討。対象地域は
  日本・米国とし、GDPR対応負荷を避けるため欧州(EEA)・英国・スイスは対象外と明記
- **集客チャネルの展開方針を決定（2026-08-02）**: `expert-panel-review`的な検討により、
  手間対効果の順で優先順位を決定。PWA化・SEOランディングページ・タイムアウト引き上げは
  実装・確認済み。S&P500ユニバース追加(TODO#8)は集客チャネル展開後に着手する方針
  (現在も未着手、`TODO.md`本体参照)

## mainマージ前の最終確認（2026-08-02）

`expert-panel-review`での検討結果、TODO#1〜#3(対応試行含む)までの内容でマージ可能と判断
（技術的にはブロッカーなし、TODO#4以降はマージ後のフォローアップとして扱う）。

最終確認で、`app_settings.period_mode`が本来のMID(既定)からSHORTに意図せず変わっている
のを発見・復元した。一連のテスト操作のどこかで変更が紛れ込んだと見られるが、原因を
特定しきれなかった。**教訓**: 長時間の実AWS環境テスト後は、必ず本番データの最終状態
(watchlist・app_settingsなど)を確認してから区切ること。

## テスト実施状況（2026-08-02）

JSTQBベースのアドホックテストを実施（`docs/test-plan.md`, `docs/test-cases.md`,
`docs/test-results/2026-08-02.md`参照）。新規欠陥は見つからず（既知のTODO#1・#7を
再確認したのみ）。テスト容易性の懸念を「7a」として追記し、後日対応済み。

## 備考: 秘密情報の扱い（2026-08-06、対応不要と判明）

開発中、AWSアクセスキーや招待トークンをチャット・平文ファイルでやり取りした。

**再調査の結果(2026-08-06)、対応不要と判明**: Lambdaの環境変数(`SES_SENDER`・
`SES_RECIPIENT`・`TOP_N`等)は秘密情報ではなく、コード中の「token」はDynamoDB管理の
招待トークン変数名で、ハードコードされた認証情報はコード・環境変数のどこにも
存在しない。「Secrets Managerへの移行」という対応は的外れで、実際の問題は
**開発セッション中にAWS認証情報をチャット・平文ファイルで共有してしまう運用**
そのものだった(2026-08-05のセキュリティインシデントの根本原因もこれ)。
対応は既に`.gitignore`のパターン追加で実施済み。今後も同様の運用(チャット・
リポジトリへの平文での認証情報記載)を避けることを徹底する。

なお開発用の一時IAMユーザー（`diag-readonly-temp`, `deploy-temp`）は削除済み。
`claude-deploy` は常設のデプロイ用ユーザーとして残してある。

---

## 優先度: 高 のうち対応済み

### 1. エラー通知がない — 対応済み（2026-08-02）

`handler()`を`_dispatch()`にリネームし、`handler()`側でtry/exceptして例外発生時にSESで
エラーメール（スタックトレース付き）を送るようにした。通知後は元の例外を再送出するので、
CloudWatchのErrorsメトリクスには引き続き記録される（通知が失敗を握りつぶさない）。
デプロイ済み（`daily-batch`、CodeSha256確認済み）。

### 7b. 本番で「例外捕捉→通知」経路を安全に検証する仕組みがない — 対応済み（2026-08-02）

`event.get("_test_trigger_failure")`があれば実データに一切触れず即座に例外を送出する
フックを追加。実AWS環境で2回invokeし、両方とも`FunctionError: Unhandled`
（CloudWatchのErrorsメトリクスに記録される）で終了し、通知メール送信が実際に
成功していることを確認済み。`tests/test_error_notification.py`でユニットテストも追加。

### 2. 外部API障害に対する耐性がない — 対応済み（2026-08-02）

`run_selection_report`で取得成功率が`MIN_FETCH_SUCCESS_RATIO`(50%)を下回ったら
例外を送出し、空同然のレポートを送らずTODO#1のエラー通知経路に流す。
NASDAQ100構成銘柄リストを`app_settings`テーブル(DynamoDB)にキャッシュし、
Nasdaq API障害時のフォールバックとして使用。

### 13. 監視銘柄のティッカー検証が形式チェックのみで、実在確認をしていない — 対応済み（2026-08-03）

`app_settings`にキャッシュ済みのNASDAQ100構成銘柄リストと突き合わせて存在確認する
`_is_known_ticker()`を追加。キャッシュが未生成・読み取り失敗の場合はfail-open(許可)。
存在しない場合は明示的なエラーメッセージを返す。テスト4件追加。

## 優先度: 中 のうち対応済み

### 4. 自動テストとCIがない — 対応済み（2026-08-02）

4段階すべて完了。`tests/`にpytest基盤、`fake_dynamodb.py`での軽量fake、
`_test_trigger_failure`イベントフラグでの障害通知経路の実地確認、
`.github/workflows/tests.yml`でのCI追加。

### 5a. 画面・エラーメッセージの文言が文書化されていない — 対応済み（2026-08-02）

`docs/requirements.md`の「5. 画面文言・エラーメッセージ一覧」として追記。

### 5. 監視・ログの可視化がない — 対応済み（2026-08-02）

CloudWatch Alarm `daily-batch-errors`+SNS+メール通知を実装。TODO#1のSES直接通知が
機能しない場合への二重の安全網として位置づけ。`_test_trigger_failure`で実地確認済み。

### 6. 招待トークンの管理が手作業 — 対応済み（2026-08-02、方針転換）

「外部公開」の方針を受けて、ダブルオプトイン方式のセルフサーブ登録を実装。
管理者(運営者)の関与は一切不要。

### 7a. handler()の日付をテスト時に注入できない — 対応済み（2026-08-02）

`event.get("simulate_date")`があればそれを使うようにした。送信可否判定を
`should_send_selection()`という純粋関数に切り出し、全パターンをユニットテスト化。

### 7. Function URL のリソースポリシーが広い — 調査済み、IAMでは解決不可（2026-08-02）

`lambda:InvokeFunction`アクションにはFunction URL経由限定の条件を付けられないという
AWS API自体の制限と判明。リスク再評価の結果、実害は「同時実行数を他人に消費される」
程度の妨害行為に留まり認証バイパスにはならないと判断し、記録に留めて対応終了。

### 14. アクセス用リンクを紛失すると再発見の導線が分かりにくい — 対応済み（2026-08-03）

`_handle_signup`が既に持っていたアクセス用リンク再送の仕組みへの案内を、
申込みフォームに一文追加。

## 優先度: 低 のうち対応済み

### 9. 選定レポートの上位件数を10位まで拡張 — 対応済み（2026-08-02）

`TOP_N`環境変数の値を3→10に変更。

### 12. デプロイの自動化 — 対応済み（2026-08-06）

GitHub ActionsからOIDC連携でLambda 2本のコードを自動デプロイするパイプラインを構築
(詳細は上部「CI/CD自動デプロイパイプラインの構築」参照、PR #21)。
