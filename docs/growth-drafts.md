# 集客用の下書き文面（2026-08-09更新、note・X投稿済み・実際の公開文面を反映）

PegSignalを実際に人の目に触れさせるための投稿文面の下書き。**実際の投稿は
ユーザー自身の判断・アカウントで行うこと**（Claudeは代行しない）。

内容はいつでも編集して良い。ただし次の2点は金商法対応上、削らないこと:
- 無償であること
- 投資助言・個別銘柄の売買推奨ではなく、機械的な計算結果の共有であること

**共有する URL は `https://pegsignal.com/` を使うこと。**
2026-08-07のドメイン統合により、`pegsignal.com`(apex)自体が登録不要の
プレビューページ(選定結果+埋め込み申込みフォーム)になった。以前は
「登録フォームだけのpegsignal.com」と「実データが見えるpreview.pegsignal.com」
を使い分けていたが、その区別は無くなっている(`preview.pegsignal.com`は
内容が重複したため無効化済み)。英語版は `?lang=en` を付ける
(`https://pegsignal.com/?lang=en`)。

**チャネルごとのアカウント方針(2026-08-08決定)**:
- **note**: 既存アカウント「お金はおっかねえ」(個人の株式投資ブログとして
  過去に十数本投稿実績あり)を使う
- **X**: 「お金はおっかねえ」と同じ名義で、**個人の開発アカウントとして新規作成**。
  PegSignal専用アカウントにはしない(noteとの一貫性を優先し、今後別の
  プロダクトを出すときも同じアカウントで発信できるようにするため)。現状の
  プライベートアカウントは非公開のため集客には使わない
- **Hacker News**: 新規作成済みのアカウント「Cashphobia」を使う

**流入元計測(`?src=`)について(2026-08-08追加)**: landing-preview
(`mnt/user-data/outputs/aws_deploy/lambda_preview/handler.py`)に
register-stock側と同じ`?src=`規約でのアクセスログを追加した(develop環境への
反映待ち、本番反映はユーザー承認後)。下記の各リンクには
`?src=note`・`?src=x`・`?src=hn`を付けている。CloudWatch Logsで
`[ACCESS] preview ... src='note'`のように検索すればチャネル別の実アクセス数が
追える。リンク自体の見た目は`pegsignal.com/?src=note`のようになり不自然では
ないので、そのまま投稿に使ってよい。

---

## 1. note.com向け紹介記事（日本語・長文）— 2026-08-09公開済み

**公開済み**: `note.com/tender_tulip54`に公開済み(自動多言語対応で英語版も
生成される、`hl=en`パラメータで閲覧可能)。以下は実際に公開した内容の
確定版(ユーザー執筆の下書きを、実装との突き合わせで校正した最終形)。
今後noteの追記・別記事執筆時の参考にする。

```
Claude Codeを使って、投資情報を「受動的に」受け取れるサービスを作りました。
開発期間はわずか1週間。AIの力を借りて、実用的なツールに仕上がりました。

### 💡 コンセプト
アクセスすればその場でランキングとシグナルが見られる。銘柄選びも設定も
不要。気に入ったら、メール登録で自動的に受け取ることもできます。

### 🛠 主な機能

1. 米国株 低PEG成長株ランキング（登録不要）
NASDAQ100・S&P500の構成銘柄から、利益成長率が一定水準以上かつPEGレシオ
（株価収益率÷利益成長率）が低い銘柄をランキング表示します。

2. テクニカルシグナル判定（登録不要）
ランキング上位の各銘柄について、トレンド・モメンタム・出来高のシグナル
を自動判定。ランキングと同じ画面でそのまま確認できます。

3. 定期レポート配信（要メール登録）
短期・中期・長期のいずれかを選ぶと、その頻度（毎日／毎週／月1回）で
最新のランキングとシグナルが届きます。

4. 個別銘柄のデイリー監視（要メール登録）
気になる銘柄を登録すると、毎日シグナルの判定結果をメールでお知らせします。

### ✉️ 開発者の想い
ボラティリティの高い相場だからこそ、長期的な指標である「PEG」が効いて
くると考えています。

ただ、私自身が「ズボラ」でサイトを見に行くのを忘れがち。だからこそ、
レポートが勝手に届く仕組みにこだわりました。メール登録に抵抗がある方は、
URLをホーム画面に保存して好きなときに開けば、ランキングとシグナルは
いつでも見られます。

効率的に情報を集めたい方は、ぜひ一度試してみてください！

※本サービスは無料です。公開データに機械的な計算式を適用した結果を共有
するもので、投資助言や特定銘柄の売買推奨ではありません。投資判断はご自身
の責任でお願いします。
```

**校正で直した主なポイント(実装との突き合わせで発見)**:
- 「低PEG銘柄」→「低PEG**成長株**」: `ranking.py`の`passes_gate()`は
  利益成長率20%以上のゲートも課しているため、成長性を問わない書き方は
  不正確だった
- 「設定した頻度で配信」→「短期・中期・長期のいずれかを選ぶとその頻度で」:
  自由な頻度設定ではなく、`handler.py`の`should_send_selection()`が
  期間モードから機械的に頻度(毎日/毎週/月1回)を決める仕組みなので、
  自由設定であるかのような表現を修正
- 機能1・2に「(登録不要)」、機能3・4に「(要メール登録)」を明記。当初は
  「自動で受け取る」ことばかり強調していたが、実際のページ体験(即座に
  ランキング+シグナルが見られ、登録導線は最下部)と一致させ、「ランキング
  だけでなくシグナルも登録不要で見られる」という他社との差別化点も明示
- 免責文言(無償・投資助言ではない旨)がユーザーの原文に含まれていることを
  確認済み(上記は校正版、実際の文言は多少異なる可能性がある)。noteの
  自動翻訳機能(2026-05-27開始)による英訳も、ニュアンスが正確であることを
  確認済み

---

## 2. X（Twitter）向け短文案（日本語）— 2026-08-09投稿済み

**アカウント**: @Cashphobia_（表示名「お金はおっかねえ」、個人開発アカウント
として新規作成済み）。プロフィールは日本語の経歴文をメインに、末尾に英語の
要約1行を添えた構成（X bio 160字制限に収まる形）。

**実際に投稿した文面（日本語、1件目）**:
```
投資情報を「受動的に」受け取れるサービスを作ってみました。PEGランキング
とシグナルを簡易的に見れます。興味あれば試してもらえると嬉しいです。

サービスURL
pegsignal.com

サービスに関するnote
note.com/tender_tulip54...
```
バナー画像(`og-image.jpg`)付きでカード表示された。**投稿時に`?src=`を
付け忘れた**ため、このポストからの流入はCloudWatch Logsでの計測から漏れる
(実害は無いが、次回以降は`?src=x`を付けること)。

**英語版（2件目、海外向け）**:
```
Free PEG-ratio screener for NASDAQ100/S&P500 stocks. No signup
needed — just open and look. Opt-in email delivery if you want it.

Not financial advice, just math on public data.

https://pegsignal.com/?lang=en&src=x

#buildinpublic #indiehackers
```
X自体の自動翻訳ボタンは「見た人向け」で発見性には効かないため、
`#buildinpublic`・`#indiehackers`という海外インディーハッカー界隈の定番タグを
使った英語版を別投稿した。リンクに`?lang=en`を付けないと英語圏の訪問者が
日本語ページに着地してしまう点に注意(landing-previewの`?lang=`は地理判定
より優先されるが、地理判定に任せず明示した方が確実)。

**案A/案B（未使用、機能訴求寄りの別案として残す）**:
```
銘柄選びもウォッチリスト作りも不要、ただ「PEGが低い成長株」を眺めるだけの
無料サービスを個人開発しました。NASDAQ100 / S&P500対応、短期・中期・長期の
3期間でランキング。登録不要、開けばすぐ見られます。
投資助言ではなく機械的な計算結果の共有ツールです。
https://pegsignal.com/?src=x
#個人開発 #投資 #米国株
```
```
週末に作った「PEGが低い成長株をランキングするだけ」の無料サービス、
地味に便利なので個人開発クラスタの人にも見てほしい。自分で銘柄を探す
手間なく、受け身でぼーっと眺められる。登録不要。
AWS Lambda + DynamoDBのサーバーレス構成。
https://pegsignal.com/?src=x
#個人開発
```

---

## 3. Hacker News "Show HN" 向け英文案（2026-08-09時点: 保留中）

note・X(日本語・英語)の投稿を優先し、Hacker Newsは一旦保留とした。
理由: HNは1つのスレッドにコメントが集中し、技術的な質問への即応が期待される
「一発勝負」形式で、ユーザーが英語での即応対応に不安を感じたため。Xの
`#buildinpublic`投稿の方が低プレッシャーで海外展開できると判断し、まずは
そちらを優先した。下記の文面・手順はそのまま使えるので、後日投稿する
気になった場合はここを参照する。

**タイトル案**: Show HN: PegSignal – a PEG-ratio stock ranking you just
look at, no account, no watchlist to set up

**本文案**:
```
I built a small, free service for people who want to glance at "which
growth stocks look cheap by PEG right now" without picking tickers or
building a watchlist themselves. It's meant to be passive: you open
the page, it's already ranked, you read it and leave.

It screens NASDAQ100 (or S&P 500) constituents for growth stocks with
a low PEG ratio (P/E ÷ earnings growth rate) and ranks them across
three time horizons (short, mid, long term) — no signup needed to
read it:
https://pegsignal.com/?lang=en&src=hn

If you do give it an email, you can additionally pick individual
stocks to track — it then watches trend (moving-average crossovers),
momentum (RSI), and volume on those and emails you when something
changes — but that's opt-in on top of the passive default, not a
requirement.

It's not investment advice — it mechanically applies fixed formulas to
public data (via yfinance) and shows the result as-is. No login, no
app. Built solo on AWS Lambda + DynamoDB.

Happy to answer questions about the design (mode presets, signal
logic, the multi-tenant DynamoDB layout, etc.) — this is a solo indie
project so any feedback is very welcome.
```

**投稿手順(Hacker News)**:
1. https://news.ycombinator.com/ にログイン
2. 右上メニューから「submit」(投稿)を選択
3. Titleに上記タイトル案を入力
4. URLは空欄のまま(テキスト投稿にする)、Textに上記本文案を貼り付け
   (URLとTextの両方は入力できないため、リンクは本文内に書く形でよい)
5. 「submit」で投稿完了

---

## 投稿状況(2026-08-09時点)

- **note・X(日本語・英語)とも投稿済み**。3チャネルとも公開済みで、次の
  投稿判断待ち(Hacker Newsは保留、Instagramは見送り。詳細は`TODO.md`
  「集客の実行フェーズ着手」節参照)
- OGP/Twitter Cardメタタグ・シェア用バナー(日英別)・PWAアイコン・
  canonical/robots.txt/sitemap.xmlの整備、いずれも本番反映・動作確認済み
  (`TODO.md`参照)
- 正方形のPEGロゴ(`docs/assets/pegsignal-icon-square.png`)はX等の
  プロフィール画像に手動でアップロードして使う(コード側には組み込んでいない)
- **未実施**: `?src=note`・`?src=x`のアクセス数をCloudWatch Logsで確認し、
  効果測定として記録すること(X投稿の1件目は`?src=`を付け忘れているため、
  そのポスト由来のアクセスは計測から漏れる点に注意)

## 補足: 投稿タイミング・注意事項

- 実ブラウザでの疎通確認は済んでいる（2026-08-07、ユーザーが実機ブラウザで
  `pegsignal.com`の表示を確認済み）
- Hacker Newsは"Show HN"のフラット投稿ルール（過度な宣伝色を避ける・
  質問への誠実な回答）を守ること。特に最初の1〜2時間はコメントに目を通し、
  質問には誠実に答える(HNは開発者からの技術的な質問が多い)
- 投稿後にアクセス数・登録数の変化が出たら、運用ダッシュボード
  (https://claude.ai/code/artifact/7fcc4958-e0a7-4ecb-a5f1-b17402696276)や
  `TODO.md`に記録すると次の判断材料になる
