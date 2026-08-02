# 残課題

最終更新: 2026-08-02

## 外部公開に向けた進行中の作業（2026-08-02）

「誰でも申し込める」形での外部公開を進めている。

- **SES本番アクセス申請: 却下済み（2026-08-02判明）**。`aws sesv2 get-account`で確認したところ
  `ProductionAccessEnabled: false`、`ReviewDetails.Status: DENIED`（CaseId: 177263262900237）。
  「審査待ち」ではなく既に却下されていたことが判明。申請時の`WebsiteURL`が
  `aws-mocha1-first-try.com`（過去の別サービス用の仮ドメイン）になっていたことが
  却下理由と推測される。**対応中**: サービスにふさわしい新ドメイン`pegsignal.com`を
  Route53で取得申請済み（反映待ち）。取得完了後、サービス説明・ダブルオプトイン方式・
  プライバシーポリシー/利用規約への導線を含むサイトを用意し、SES本番アクセスを再申請する
- マルチテナント化（TODO#6a）は対応済み
- **マネタイズ・国際展開方針を決定（2026-08-02）**: 決済機能は持たず、広告
  （非パーソナライズ・Web画面限定）またはアフィリエイトで検討。対象地域は
  日本・米国とし、GDPR対応負荷を避けるため欧州(EEA)・英国・スイスは対象外と明記。
  `docs/privacy-policy.md`・`docs/terms-of-service.md`を新設し、register-stock画面
  （申込みフォーム・メイン画面）に導線を追加済み
- 残り: TODO#7（Function URLポリシー）、乱用・コスト対策の強化、UI/CSS装飾（多言語化と同時に実施予定）
- **集客チャネルの展開方針を決定（2026-08-02）**: `expert-panel-review`的な検討により、
  手間対効果の順で以下の優先順位とした。
  1. PWA化（`manifest.json`追加、ほぼゼロコスト）
  2. SEO対応の公開ランディングページ（`pegsignal.com`取得・SES再申請サイトと統合して実施）
  3. Lambdaタイムアウトを900秒に引き上げ（S&P500導入への事前準備、低コスト）
  4. （利用者が増えてから）S&P500ユニバース追加（TODO#8、TODO#3のタイムアウト対策未解決を
     踏まえ、5倍の銘柄数でのリスクを避けるため集客より後回しにする）
  5. X(旧Twitter)自動投稿は実装コストは低いがAPI利用料が発生するため、費用確認後に判断
  6. ネイティブスマホアプリ・Instagram連携は当面見送り（手間対効果が低い）
  - [x] 1. PWA化対応済み。register-stock画面に`manifest.json`(`?manifest=1`)・
    アイコン(SVG data URI)・apple-mobile-web-app系メタタグを追加。
    `start_url`は意図的に省略し、招待トークン付きURLがそのまま起動先になるようにした
  - [x] 3. daily-batchのタイムアウトを300秒→900秒に変更（`deploy.sh`）。
    **この環境のAWS認証情報が無効なため未反映。次回`deploy.sh`実行時に反映される**
  - [x] 2. `pegsignal.com`取得完了（2026-08-02）。別サイトは新設せず、既存の
    register-stock Lambdaが返すHTMLをCloudFront経由でそのまま出す方針とした
    （コンテンツの二重管理を避けるため）。実装内容:
    - `<title>`・`meta description`をLambdaのHTML出力に追加（今までSEOタグが皆無だった）
    - 申込みフォーム(未登録者にも見える実質的な公開ランディングページ)にPEGレシオでの
      選定方法・シグナル監視の説明文を追加
    - `?info=1`（仕組みの説明ページ）をトークン必須のガードから外し、未登録者でも
      読めるように変更（SEO・信頼性向上のため）
    - `deploy_domain.sh`を新規作成: ACM証明書(DNS検証)・CloudFrontディストリビューション
      （オリジン=register-stockのFunction URL、CachingDisabled+
      AllViewerExceptHostHeaderでクエリ文字列を全転送）・Route53 ALIASレコードを設定し、
      `pegsignal.com`をそのままLambdaの出力に向ける
    - **このスクリプトは未実地検証**（この環境のAWS認証情報が無効なため）。
      実行後は`https://pegsignal.com/`・`?privacy=1`・`?info=1`の表示確認が必要
    - 2026-08-02 追記: `deploy_domain.sh`にべき等性の考慮漏れがあったため修正。
      失敗して再実行した場合にACM証明書・CloudFrontディストリビューションを
      重複作成してしまう問題があり、それぞれ既存リソースの検索・再利用を追加した。
      （`aws`コマンド自体はこの環境に存在しないため実行検証は依然できておらず、
      構文チェック(`bash -n`)と`tests/test_register_handler.py`(23件)の
      パスのみ確認済み。実地でのAWSコマンド実行確認は引き続き必要）
  - [ ] 4. S&P500追加は集客チャネル展開後

## mainマージ前の最終確認（2026-08-02）

`expert-panel-review`での検討結果、TODO#1〜#3(対応試行含む)までの内容でマージ可能と判断
（技術的にはブロッカーなし、TODO#4以降はマージ後のフォローアップとして扱う）。

最終確認で、`app_settings.period_mode`が本来のMID(既定)からSHORTに意図せず変わっている
のを発見・復元した。一連のテスト操作のどこかで変更が紛れ込んだと見られるが、原因を
特定しきれなかった。**教訓**: 長時間の実AWS環境テスト後は、必ず本番データの最終状態
(watchlist・app_settingsなど)を確認してから区切ること。`jstqb-test-process`スキルの
Test Completion活動にこのチェックを追記すべき。

## テスト実施状況

2026-08-02にJSTQBベースのアドホックテストを実施（`docs/test-plan.md`,
`docs/test-cases.md`, `docs/test-results/2026-08-02.md`参照）。新規欠陥は
見つからず（既知のTODO#1・#7を再確認したのみ）。テスト容易性の懸念を
新規に「7a」として追記。

現状は「自分ひとりで使う分には動く」状態。以下は公開・複数人利用に向けて未着手の項目。
優先度は「壊れたときの困り度 × 起きやすさ」で並べている。

---

## 優先度: 高（放置すると黙って壊れる）

### 1. エラー通知がない — 対応済み（2026-08-02）

`handler()`を`_dispatch()`にリネームし、`handler()`側でtry/exceptして例外発生時にSESで
エラーメール（スタックトレース付き）を送るようにした。通知後は元の例外を再送出するので、
CloudWatchのErrorsメトリクスには引き続き記録される（通知が失敗を握りつぶさない）。

検証: ローカルで`run_monitoring_report`を意図的に例外送出させ、通知メール送信の呼び出しと
例外の再送出の両方を確認（コンポーネントレベル）。実際の`ses.send_email`呼び出し自体は
通常の監視レポート送信で同日中に実地確認済みの同一関数を再利用しているため、追加の
本番環境での障害注入テストは行っていない（動作中のシステムに意図的な障害を注入する
リスクとのトレードオフ。`test-skip-review`スキルでのレビュー結果は下記7bを参照）。
デプロイ済み（`daily-batch`、CodeSha256確認済み）。

### 7b. 本番で「例外捕捉→通知」経路を安全に検証する仕組みがない — 対応済み（2026-08-02）

TODO#4段階3で対応。`event.get("_test_trigger_failure")`があれば実データに一切触れず
即座に例外を送出するフックを追加。実AWS環境で2回invokeし、両方とも
`FunctionError: Unhandled`（CloudWatchのErrorsメトリクスに記録される）で終了し、
かつ`--log-type Tail`のログに通知メール送信失敗の警告が出ていないことから、
`_notify_failure`のSES送信が実際に成功していることを確認済み。
`tests/test_error_notification.py`でユニットテストも追加（3件、モック経由）。

### 2. 外部API障害に対する耐性がない — 対応済み（2026-08-02）

`expert-panel-review`スキルで専門家パネル（クラウドインフラエンジニア/セキュリティ
エンジニア/FinOpsエンジニア）による検討を実施し、以下で実装:

- `run_selection_report`で取得成功率が`MIN_FETCH_SUCCESS_RATIO`(50%)を下回ったら
  例外を送出し、空同然のレポートを送らずTODO#1のエラー通知経路に流す
  （監視レポート側は対象銘柄数が少なく閾値判定になじまないため対象外、
  従来通り取得できた銘柄だけ載せる動作を維持）
- NASDAQ100構成銘柄リストを`app_settings`テーブル(DynamoDB)にキャッシュし、
  Nasdaq API障害時のフォールバックとして使用（S3は使わず既存のDynamoDBアクセスを流用）

検証: ローカルで成功率75%(正常送信)・25%(例外送出)の両方をユニット相当で確認、
実AWS環境で選定レポートを実行しキャッシュ保存を確認。実装時に`daily-batch`の
実行ロールに`app_settings`への`dynamodb:PutItem`権限が不足していたことが判明し、
インラインポリシーを追加して解決（デプロイ後すぐに気づけたのは今回のテストが
あったため）。

### 3. 銘柄ユニバース拡張時のタイムアウト — 対応試行、効果なし（2026-08-02）

現在 NASDAQ100(103銘柄) で実測 **41秒**、タイムアウト設定は 300秒なので余裕がある。
ユニバース拡張(TODO#8)は現時点で未着手・未計画のため、今すぐ困ってはいない。

`expert-panel-review`でクラウドインフラ/SRE/システム設計者による検討を行い、
`concurrent.futures.ThreadPoolExecutor(max_workers=10)`で並列取得を実装したが、
**実AWS環境で計測したところ効果がほぼなかった（並列化後も約39〜40秒、
並列化前の41秒とほぼ同じ）**。

原因の仮説: `yfinance`が内部で全スレッド共有の単一セッションオブジェクト
(`YfData`シングルトン、`curl_cffi`のSession)を使っており、並列アクセスに対して
実効的に高速化されない実装になっている可能性がある。このセッションが乗っている
`data.py`にCookie/crumb取得用の明示的なロック(`_cookie_lock`)があることは確認したが、
それだけでは今回の遅さを説明しきれておらず未確定。この環境からは実際の
Yahoo Financeへの通信がネットワークポリシーでブロックされており、これ以上の
直接検証はできなかった。

コード自体は害がない（並列化しても正しく動作する、ただ速くなっていないだけ）ため
残しているが、**タイムアウト対策としては未解決**として扱う。

対応案（未着手）:
- 銘柄取得スレッドごとに別々の`requests`/`curl_cffi`セッションを使う
  （`yf.Ticker(t, session=...)`でシングルトン共有を回避できるか要検証）
- 銘柄群を分割して複数回の Lambda 実行に分ける
- タイムアウトを 900秒(AWS上限)まで引き上げる（根本対策ではないが応急措置にはなる）

**着手条件**: TODO#8（ユニバース拡張）に着手するタイミングで、実際にYahoo Financeへ
通信できる環境で改めて検証すること。

---

## 優先度: 中（今は困らないが、育てるなら要る）

### 4. 自動テストとCIがない — 段階1完了（2026-08-02）

`expert-panel-review`での検討により4段階に分割して着手:

- [x] 段階1: `tests/`にpytest基盤を作成。`signals.py`のシグナル計算(`_cycle_window`の
      休日除外・タイムゾーン警告なし、RSI対称閾値、出来高の自己参照なし)と
      `handler.py`の選定レポート送信可否判定(`should_send_selection`、モード×曜日×
      月初日の全パターン)をユニットテスト化。20件全てpass。副産物として
      `should_send_selection`を独立関数に切り出し、TODO#7a(日付注入)も
      `simulate_date`イベントフラグで解決（実AWS環境で動作確認済み）
- [x] 段階2: `tests/fake_dynamodb.py`にDynamoDBの軽量fakeを実装し、register-stock
      (`mnt/user-data/outputs/aws_deploy/lambda_register/handler.py`)のコンポーネント
      テストを追加（認証、ティッカー登録・削除・重複・検証、監視リスト上限、
      期間モード切替）。daily-batch側と同名の`handler.py`が別ディレクトリに存在する
      問題は`importlib.util.spec_from_file_location`で別名ロードして回避。
      合計31件全てpass
- [x] 段階3: TODO#7b対応。`_test_trigger_failure`イベントフラグを追加し、実AWS環境で
      障害通知経路(例外送出→CloudWatch記録→SES通知)を実地確認。ユニットテスト3件追加
      (合計34件全てpass)
- [x] 段階4: `.github/workflows/tests.yml`でpush/PR時に自動実行するCIを追加。
      クリーンな仮想環境で`requirements-dev.txt`のみをインストールした状態での
      pytest実行をローカルで事前検証済み（34件全てpass）

**TODO#4は4段階すべて完了。**

対応案（元の記録）:
- DynamoDB は 20行程度のインメモリfakeで代用できる（moto はこの環境では
  cryptography/pyo3 依存が壊れていて動かなかった）

### 5a. 画面・エラーメッセージの文言が文書化されていない

register-stockの画面文言・エラーメッセージはコード内(`_render_form`/`_render_info`等)
にしかなく、一覧化された文書がない。`expert-panel-review`での検討結果、独立した
「製品仕様書」を新設するほどではないが（`docs/requirements.md`が実質その役割を
既に果たしている）、文言一覧はいずれ`docs/requirements.md`への軽い追記で対応する
価値がある。優先度は低い。

### 5. 監視・ログの可視化がない — 対応済み（2026-08-02）

`expert-panel-review`での検討結果、ダッシュボードまでは作らずCloudWatch Alarm(1個)
+SNS+メール通知のみをコスト($0.10〜0.20/月程度)を確認した上で実装。TODO#1のSES直接
通知が機能しない場合（Lambda自体が起動できない等）への二重の安全網として位置づけ。

- CloudWatch Alarm `daily-batch-errors`(`AWS/Lambda`の`Errors`メトリクス>=1で発火)
- SNSトピック`daily-batch-alarm-topic`経由でメール通知（購読確認済み）
- `_test_trigger_failure`で実際に例外を発生させ、Alarmが`INSUFFICIENT_DATA`→`ALARM`
  に遷移し、メール通知が実際に届くことを実地確認済み
- `deploy.sh`にも追加済み（新規環境でも同じ構成が再現される）
- 詳細は`docs/architecture.md`の「3a. 監視・通知リソース」を参照

ダッシュボードは今回作らなかった（TODO.md備考: 追加で$3/月かかるため、必要になったら
別途検討）。

### 6. 招待トークンの管理が手作業 — 対応済み（2026-08-02、方針転換）

「外部公開」の方針（誰でも申し込める、人数上限は決めていない）を受けて、当初想定していた
「管理者が手動承認するUI」ではなく、**ダブルオプトイン方式のセルフサーブ登録**を実装した
（`expert-panel-review`での検討結果、管理者の手を煩わせない一般的な方式として採用）。

- `register-stock`に`?signup_email=...`での申込み受付を追加。簡易CAPTCHA(計算問題)付き
- 確認メール内のリンク(`?token=...&confirm=1`)をクリックすると
  `approved_tokens`の`status`が`pending`→`approved`に自動昇格
- 管理者(あなた)の関与は一切不要

### 6a. マルチテナント化（利用者ごとのデータ分離） — 対応済み（2026-08-02）

TODO#6のセルフサーブ化に伴い、サービス全体が「1人専用」の設計だったことが判明
（`watchlist`・期間モードが全利用者で共有されていた）。以下を実装:

- `watchlist`テーブルを`owner_token`(HASH)+`ticker`(RANGE)の複合キーに作り直し
  （既存データはNVDA/AAPLの2件のみだったため手動移行）
- 期間モードを`app_settings`に`"{token}#period_mode"`という利用者ごとのキーで保存
- `signal_history`は利用者ごとではなく「期間モードごと」に共有（キーに`#MODE`を付与）
  し、同じモードの利用者間で計算結果を使い回すことでコストを抑制
- `daily-batch`の選定レポート送信は、利用者ごとではなく**使われているモードごとに
  1回だけNASDAQ100をスキャン**し、そのモードの利用者全員に個別リンクで配信する方式に変更
- 監視レポートは利用者ごとに自分のwatchlist・モードで個別集計・個別送信
- IAM権限を両ロールに追加(`dynamodb:Query`on watchlist、`approved_tokens`への
  GetItem/Scan/PutItem、register-stock-roleへの`ses:SendEmail`)
- ユニットテスト14件追加（他人の登録に影響しないこと、モードごとのスキャン共有、
  利用者ごとの個別送信など）、実AWS環境で2利用者による分離動作を確認済み
- `deploy.sh`に新スキーマ・新IAM権限を反映済み

**副次的に発覚したドリフト**: `deploy.sh`が想定するIAMロール名(`lambda-daily-batch-role`
等)と実際のデプロイ済みロール名(`stock-report-batch-role`等)が異なっていた。実害はないが
`deploy.sh`側のロール名を実態に合わせて修正すべき（詳細は`docs/architecture.md`5節）。

**未対応（次点）**: IPごとのレート制限、選定レポートがモード別スキャンになったことによる
実行コスト増（最大でモード数倍、現状3モードなので最大3倍）の実測確認。

### 7a. handler()の日付をテスト時に注入できない — 対応済み（2026-08-02）

TODO#4段階1で対応。`event.get("simulate_date")`があればそれを使い、なければ
`datetime.now()`を使うようにした。あわせて送信可否判定を`should_send_selection()`
という純粋関数に切り出し、`tests/test_report_frequency.py`でモード×曜日×月初日の
全パターンをユニットテスト化。実AWS環境でも`simulate_date`指定での動作を確認済み。

### 7. Function URL のリソースポリシーが広い — 調査済み、IAMでは解決不可（2026-08-02）

`public-url-invoke-function` の許可が無条件の `Principal: *` になっており、
「Function URL経由に限る」という条件が付いていない。

**実地検証の結果**: `add-permission`に`--function-url-auth-type NONE`を
`lambda:InvokeFunction`アクションへ付けようとしたところ、
`InvalidParameterValueException: FunctionUrlAuthType is only supported for
lambda:InvokeFunctionUrl action`で拒否された。これはCLIの制限ではなく**AWSのAPI
自体の制限**で、`lambda:InvokeFunction`アクションにはこの条件を一切付けられない
（コンソールでURLを作り直しても同じ制限に当たる可能性が高く、「コンソールなら直る」
という当初の想定は誤りだった可能性がある）。

**リスク再評価**（`expert-panel-review`での検討結果）: この権限が意味するのは
「AWSアカウントを持つ第三者がURLを経由せず直接Lambdaをinvokeできる」ことだが、
呼び出しても実行されるコードは同じであり、`_is_valid_token`によるトークン検証は
アプリケーション層で無条件に行われる。実害は「同時実行数(上限2)を他人に消費される」
程度の妨害行為に留まり、認証バイパスにはならない。IAMポリシーでの根本対応は
できないため、優先度を下げて記録に留める。

---

## 優先度: 低（将来の機能追加）

### 8. 銘柄ユニバースの切り替え（S&P500 / NYダウ / 日経）

画面のプルダウンで対象指数を切り替えられるようにしたい。
期間モードと同じ仕組み（`app_settings` テーブル + `config.py` のプリセット）で実装できる。
**着手前に上記3（タイムアウト対策）が必要。**

### 9. 選定レポートの上位件数を10位まで拡張

`TOP_N` は既に環境変数化済みなので、値を変えるだけで対応可能。
画面から変更できるようにするなら期間モードと同じ実装で足りる。

### 10. メール以外の通知手段（LINE / Slack など）

### 11. MACD方式への切り替え

`signals.py` に実装済みだが `config.MA_OR_MACD = "MA"` に固定されていて未使用。
各期間モードの MACD パラメータ（`macd_fast`/`macd_slow`/`macd_signal`）も定義済みなので、
切り替えUIを足せば動く状態にはなっている。ただし判定ロジックの妥当性は未検証。

### 12. デプロイの自動化

現在は AWS CLI を手で叩いている。`deploy.sh` は整備済みだが CI/CD パイプラインはない。

---

## 備考: 秘密情報の扱い

開発中、AWSアクセスキーや招待トークンをチャット・平文ファイルでやり取りした。
個人利用の現状では許容範囲だが、公開サービス化を見据えるなら
Secrets Manager / SSM Parameter Store への移行を検討すること。

なお開発用の一時IAMユーザー（`diag-readonly-temp`, `deploy-temp`）は削除済み。
`claude-deploy` は常設のデプロイ用ユーザーとして残してある。
