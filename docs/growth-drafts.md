# 集客用の下書き文面（2026-08-07）

PegSignalを実際に人の目に触れさせるための投稿文面の下書き。**実際の投稿は
ユーザー自身の判断・アカウントで行うこと**（Claudeは代行しない）。

内容はいつでも編集して良い。ただし次の2点は金商法対応上、削らないこと:
- 無償であること
- 投資助言・個別銘柄の売買推奨ではなく、機械的な計算結果の共有であること

URLは公開ドメイン `https://pegsignal.com/`（日本語）/ `https://en.pegsignal.com/`
（英語）を想定。

---

## 1. note.com向け紹介記事案（日本語・長文）

**タイトル案**: 個人開発で「PEGレシオが低い成長株」を毎週メールしてくれる
無料サービスを作った話

**本文案**:

投資クラスタ・個人開発クラスタ向けに、週末で作った無料サービスを紹介します。

**PegSignal**は、NASDAQ100（またはS&P500）の構成銘柄から、PEGレシオ
（株価収益率 ÷ 利益成長率）が低い、つまり「成長のわりに株価が割高になって
いない」銘柄を自動でスクリーニングし、短期・中期・長期の3つの判定期間で
定期的にメール配信する無料サービスです。

自分で選んだ銘柄については、トレンド（ゴールデンクロス/デッドクロス）・
モメンタム（RSI）・出来高の変化もあわせて監視し、変化があればお知らせします。

**大事な注意点**: これは投資助言ではありません。公開データに機械的な計算式を
適用した結果を淡々と共有するだけのツールです。売買の判断はご自身の責任で
お願いします。

個人開発（AWS Lambda + DynamoDB構成）で、広告も課金も今のところ無し。
メールアドレスの登録だけで使えます。気になった方はどうぞ:
https://pegsignal.com/

フィードバック・不具合報告は大歓迎です。

---

## 2. X（Twitter）向け短文案（日本語）

**案A（機能訴求）**:
```
PEGレシオが低い成長株を自動スクリーニングして、週1でメールしてくれる
無料サービスを個人開発しました。
NASDAQ100 / S&P500対応、監視銘柄はトレンド・RSI・出来高もチェック。
投資助言ではなく機械的な計算結果の共有ツールです。
https://pegsignal.com/
#個人開発 #投資 #米国株
```

**案B（開発者視点）**:
```
週末に作った「PEGレシオが低い成長株を毎週メールしてくれるだけ」の
無料サービス、地味に便利なので個人開発クラスタの人にも見てほしい。
AWS Lambda + DynamoDBのサーバーレス構成。
https://pegsignal.com/
#個人開発
```

---

## 3. Hacker News "Show HN" 向け英文案

**タイトル案**: Show HN: PegSignal – a free email digest of low-PEG growth
stocks (NASDAQ100/S&P 500)

**本文案**:
```
I built a small, free service that screens NASDAQ100 (or S&P 500)
constituents for growth stocks with a low PEG ratio (P/E ÷ earnings
growth rate) and emails you a ranked list on a schedule you pick
(daily/weekly/monthly). For stocks you track, it also watches trend
(moving-average crossovers), momentum (RSI), and volume, and emails
you when something changes.

It's not investment advice — it mechanically applies fixed formulas to
public data (via yfinance) and shows the result as-is. No login, no
app; just an email address. Built solo on AWS Lambda + DynamoDB.

https://en.pegsignal.com/

Happy to answer questions about the design (mode presets, signal
logic, the multi-tenant DynamoDB layout, etc.) — this is a solo indie
project so any feedback is very welcome.
```

---

## 補足: 投稿タイミング・注意事項

- P3（実ブラウザでの外部疎通確認）を先に済ませてから投稿するのが望ましい
  （リンク切れ・403等でユーザー体験を損なわないため）
- Hacker Newsは"Show HN"のフラット投稿ルール（過度な宣伝色を避ける・
  質問への誠実な回答）を守ること
- 投稿後にアクセス数・登録数の変化が出たら、`docs/`配下のダッシュボードや
  `TODO.md`に記録すると次の判断材料になる
