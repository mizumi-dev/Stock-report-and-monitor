# 集客用の下書き文面（2026-08-07、URL更新: apex統合後）

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

---

## 1. note.com向け紹介記事案（日本語・長文）

**タイトル案**: 個人開発で「PEGレシオが低い成長株」を毎週メールしてくれる
無料サービスを作った話

**本文案**:

投資クラスタ・個人開発クラスタ向けに、週末で作った無料サービスを紹介します。

**PegSignal**は、NASDAQ100（またはS&P500）の構成銘柄から、PEGレシオ
（株価収益率 ÷ 利益成長率）が低い銘柄を自動でスクリーニングし、
短期・中期・長期の3つの判定期間でランキングする無料サービスです。

**登録不要で、今の選定結果をそのまま見られます:**
https://pegsignal.com/

メールアドレスを登録すると、さらに「自分で選んだ銘柄の監視」ができます。
トレンド（ゴールデンクロス/デッドクロス）・モメンタム（RSI）・出来高の変化を
見張って、変化があればメールでお知らせします。

**大事な注意点**: これは投資助言ではありません。公開データに機械的な計算式を
適用した結果を淡々と共有するだけのツールです。売買の判断はご自身の責任で
お願いします。

個人開発（AWS Lambda + DynamoDB構成）で、広告も課金も今のところ無し。

フィードバック・不具合報告は大歓迎です。

---

## 2. X（Twitter）向け短文案（日本語）

**案A（機能訴求）**:
```
PEGレシオが低い成長株を自動スクリーニングする無料サービスを個人開発しました。
NASDAQ100 / S&P500対応、短期・中期・長期の3期間でランキング。
登録不要でそのまま見られます（メール登録すると銘柄の監視もできます）。
投資助言ではなく機械的な計算結果の共有ツールです。
https://pegsignal.com/
#個人開発 #投資 #米国株
```

**案B（開発者視点）**:
```
週末に作った「PEGレシオが低い成長株をランキングするだけ」の無料サービス、
地味に便利なので個人開発クラスタの人にも見てほしい。登録不要で見れます。
AWS Lambda + DynamoDBのサーバーレス構成。
https://pegsignal.com/
#個人開発
```

---

## 3. Hacker News "Show HN" 向け英文案

**タイトル案**: Show HN: PegSignal – free PEG-ratio screening of
NASDAQ100 / S&P 500, no signup needed to read it

**本文案**:
```
I built a small, free service that screens NASDAQ100 (or S&P 500)
constituents for growth stocks with a low PEG ratio (P/E ÷ earnings
growth rate) and ranks them across three time horizons (short, mid,
long term).

You can read the current results without signing up:
https://pegsignal.com/?lang=en

If you give it an email address, you additionally get to pick
individual stocks to track — it then watches trend (moving-average
crossovers), momentum (RSI), and volume on those, and emails you when
something changes, on whatever schedule you pick.

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

## 補足: 投稿タイミング・注意事項

- 実ブラウザでの疎通確認は済んでいる（2026-08-07、ユーザーが実機ブラウザで
  `pegsignal.com`の表示を確認済み）
- Hacker Newsは"Show HN"のフラット投稿ルール（過度な宣伝色を避ける・
  質問への誠実な回答）を守ること。特に最初の1〜2時間はコメントに目を通し、
  質問には誠実に答える(HNは開発者からの技術的な質問が多い)
- 投稿後にアクセス数・登録数の変化が出たら、運用ダッシュボード
  (https://claude.ai/code/artifact/7fcc4958-e0a7-4ecb-a5f1-b17402696276)や
  `TODO.md`に記録すると次の判断材料になる
