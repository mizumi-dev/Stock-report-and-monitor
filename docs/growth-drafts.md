# 集客用の下書き文面（2026-08-08、チャネル決定・?src=計測を追加）

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

## 1. note.com向け紹介記事案（日本語・長文）

**タイトル案**: 銘柄も選ばず、ログインもせず、ただ「PEGが低い成長株」を
眺めるだけのサービスを作った話

**本文案**:

投資クラスタ・個人開発クラスタ向けに、週末で作った無料サービスを紹介します。

**PegSignal**は、自分で銘柄を選んだりウォッチリストを作ったりする必要が
一切ない、**受動的に眺めるだけ**のPEGランキングサービスです。NASDAQ100
（またはS&P500）の構成銘柄から、PEGレシオ（株価収益率 ÷ 利益成長率）が
低い銘柄を自動でスクリーニングし、短期・中期・長期の3つの期間でランキング
した結果をそのまま公開しています。

**アカウント登録も不要、開けばすぐ見られます:**
https://pegsignal.com/?src=note

「自分で銘柄を探すのは面倒だけど、今どんな銘柄が良さそうか知りたい」
くらいの温度感で眺めてもらうためのサービスです。メールアドレスを登録すると、
気になる銘柄だけを監視してトレンド・モメンタム・出来高の変化をメールで
知らせる機能も使えますが、それすら任意です。

**大事な注意点**: これは投資助言ではありません。公開データに機械的な計算式を
適用した結果を淡々と共有するだけのツールです。売買の判断はご自身の責任で
お願いします。

個人開発（AWS Lambda + DynamoDB構成）で、広告も課金も今のところ無し。

フィードバック・不具合報告は大歓迎です。

---

## 2. X（Twitter）向け短文案（日本語）

**案A（受動視聴訴求）**:
```
銘柄選びもウォッチリスト作りも不要、ただ「PEGが低い成長株」を眺めるだけの
無料サービスを個人開発しました。NASDAQ100 / S&P500対応、短期・中期・長期の
3期間でランキング。登録不要、開けばすぐ見られます。
投資助言ではなく機械的な計算結果の共有ツールです。
https://pegsignal.com/?src=x
#個人開発 #投資 #米国株
```

**案B（開発者視点）**:
```
週末に作った「PEGが低い成長株をランキングするだけ」の無料サービス、
地味に便利なので個人開発クラスタの人にも見てほしい。自分で銘柄を探す
手間なく、受け身でぼーっと眺められる。登録不要。
AWS Lambda + DynamoDBのサーバーレス構成。
https://pegsignal.com/?src=x
#個人開発
```

---

## 3. Hacker News "Show HN" 向け英文案

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

## 投稿前の準備状況(2026-08-08時点)

- **対応済み**: landing-previewにOGP/Twitter Cardのメタタグ(og:title・
  og:description・twitter:card)を追加。今まで完全に空だったため、X等に
  リンクを貼ると説明文の無いカードになっていた
- **対応済み(2026-08-08)**: シェア用バナー画像(横長、og:image用)をLambdaに
  組み込み済み。`pegsignal.com`へのリンクをX等に貼ると自動でバナー付きの
  カードになる(`OG_IMAGE_URL`環境変数での明示的な指定は不要)。正方形の
  PEGロゴ(`docs/assets/pegsignal-icon-square.png`)はX等のプロフィール画像に
  手動でアップロードして使う
- develop環境での動作確認・ユーザー承認後のmaster反映が必要(このリポジトリの
  開発フロー、CLAUDE.md参照)。反映後にX/note/シェアカード確認ツール等で
  実際のカード表示を確認すること

## 補足: 投稿タイミング・注意事項

- 実ブラウザでの疎通確認は済んでいる（2026-08-07、ユーザーが実機ブラウザで
  `pegsignal.com`の表示を確認済み）
- Hacker Newsは"Show HN"のフラット投稿ルール（過度な宣伝色を避ける・
  質問への誠実な回答）を守ること。特に最初の1〜2時間はコメントに目を通し、
  質問には誠実に答える(HNは開発者からの技術的な質問が多い)
- 投稿後にアクセス数・登録数の変化が出たら、運用ダッシュボード
  (https://claude.ai/code/artifact/7fcc4958-e0a7-4ecb-a5f1-b17402696276)や
  `TODO.md`に記録すると次の判断材料になる
