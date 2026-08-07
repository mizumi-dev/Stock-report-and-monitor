# 集客用の下書き文面（2026-08-07）

PegSignalを実際に人の目に触れさせるための投稿文面の下書き。**実際の投稿は
ユーザー自身の判断・アカウントで行うこと**（Claudeは代行しない）。

内容はいつでも編集して良い。ただし次の2点は金商法対応上、削らないこと:
- 無償であること
- 投資助言・個別銘柄の売買推奨ではなく、機械的な計算結果の共有であること

**共有する URL は `https://preview.pegsignal.com/` を使うこと（2026-08-07変更）。**
expert-panel-reviewでの検討結果、外部から来た訪問者の着地先としては、
登録フォーム（`pegsignal.com`）よりも、実際の選定結果がその場で読める
プレビューページの方が適切と判断した。理由:

- `pegsignal.com` の第一画面は「メールアドレスを入力してください」という
  フォームと**サンプル画像（モック）**であり、本物の中身がゼロ。冷たい訪問者に
  最初に登録を要求する形になっており、コンバージョンを著しく損なう
- `preview.pegsignal.com` は短期・中期・長期の実際のランキングがその場で読め、
  ページ下部に登録案内（監視リスト・メール配信）への導線がある。
  「価値を見せてから登録を頼む」順序になっている

登録ページ自体を案内したい場合（既に興味を持っている読者向け）は
`https://pegsignal.com/`、英語版は `https://en.pegsignal.com/` を使う。

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
https://preview.pegsignal.com/

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
https://preview.pegsignal.com/
#個人開発 #投資 #米国株
```

**案B（開発者視点）**:
```
週末に作った「PEGレシオが低い成長株をランキングするだけ」の無料サービス、
地味に便利なので個人開発クラスタの人にも見てほしい。登録不要で見れます。
AWS Lambda + DynamoDBのサーバーレス構成。
https://preview.pegsignal.com/
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
https://preview.pegsignal.com/?lang=en

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

**注意**: `preview.pegsignal.com` には英語版サブドメイン（`en.` に相当するもの）を
用意していないため、英語で見せるには `?lang=en` を付ける必要がある
（landing-previewは`X-Site-Lang`ヘッダーか`?lang=`クエリで言語を判定する）。
英語圏への露出を本格的に狙うなら、`en.pegsignal.com` と同じ要領で
英語版preview用のディストリビューションを追加することを検討する。

---

## 補足: 投稿タイミング・注意事項

- 実ブラウザでの疎通確認は済んでいる（2026-08-07、ユーザーが
  `pegsignal.com`・`preview.pegsignal.com` の両方の表示を確認済み）
- Hacker Newsは"Show HN"のフラット投稿ルール（過度な宣伝色を避ける・
  質問への誠実な回答）を守ること
- 投稿後にアクセス数・登録数の変化が出たら、`docs/`配下のダッシュボードや
  `TODO.md`に記録すると次の判断材料になる
