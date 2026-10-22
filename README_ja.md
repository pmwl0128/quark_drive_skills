<h1 align="center">Quark Drive Skills · クォークチャージ </h1>

Web・Console のチーム入力欄とインポートメニュー、AI 質問・チーム診断で公開チームコードを利用できます。1 回につき 1 コードを入力してください。運用者による独自の照会用アカウントまたはゲートウェイの設定が必要です。設定と追加依存関係は[チームコードガイド](.agents/skills/pokemon-champions-team/references/team-codes.md)を参照してください。

<p align="center">
  <img alt="skills" src="https://img.shields.io/badge/skills-4-blue"/>
  <img alt="modes" src="https://img.shields.io/badge/single%20%26%20double-supported-success"/>
  <img alt="query" src="https://img.shields.io/badge/query-offline--first-success"/>
  <img alt="languages" src="https://img.shields.io/badge/names-zh%20%7C%20en%20%7C%20ja-orange"/>
  <img alt="agents" src="https://img.shields.io/badge/Claude%20Code%20%7C%20Codex-Agent%20Skills-blueviolet"/>
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green"/>
</p>

<p align="center">
  <b>環境スナップショット</b> · M-7 / M-C · 更新日 <b>2026-10-21</b>
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README_zh.md">中文</a> | <b>日本語</b>
</p>

<p align="center">
  <a href="https://champion.mpan.top">Quark Drive Webを開く</a> · <a href="https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest">配布ファイル</a>
</p>

---

『ポケモンチャンピオンズ』向けのAgent Skillsです。図鑑・環境データの照会やダメージ計算に加え、データと所定の手順に沿った構築作成、構築診断、SP調整をAIアシスタントから行えます。シングル・ダブルに対応し、ポケモンや技の名前は日本語・英語・中国語で検索できます。

## 導入

Claude Code、Codex、WorkBuddyなど、Agent Skillsを読み込み、ローカルのPythonを実行できるアシスタントで使えます。**Python 3.10以上**を用意してください。計算器は通常`quickjs-ng`を使い、未導入の場合はNode.jsに切り替わります。図鑑・環境・構築の通常の問い合わせには、追加のPythonライブラリは不要です。

ターミナルを操作できるアシスタントには、次のように依頼できます。

> https://github.com/pmwl0128/quark_drive_skills の4つのスキルを、このアシスタントで使える場所にインストールしてください。`quickjs-ng>=0.15`を導入し、導入できなければNode.jsで計算器が動くことを確認してください。4つの`SKILL.md`が認識されることを確かめ、計算器の`schema`コマンドを実行してください。

自分で導入する場合は、利用するアシスタントに合わせて選んでください。

| アシスタント | 導入方法 |
|---|---|
| Claude Code | 下記のプラグインを使うか、リポジトリの`.agents/skills/`にある4つのフォルダーを`~/.claude/skills/`へコピーします。 |
| Codex | 4つのスキルフォルダーを`~/.agents/skills/`へコピーします。 |
| WorkBuddy | スキル画面の「スキルを追加 → スキルをアップロード」からローカルのスキルパッケージを読み込みます。[公式の操作案内](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)を参照してください。 |
| その他 | 各アシスタントが参照するスキル用ディレクトリへコピーするか、下記の`skills`コマンドを使います。 |

Claude Codeのプラグインは、アプリ内で次を実行します。

```text
/plugin marketplace add pmwl0128/quark_drive_skills
/plugin install quark-drive-skills@quark-drive
```

旧プラグインが残っている場合は、先に`/plugin uninstall pokemon-champions@pmwl`で削除してください。

Node.jsを導入済みなら、[Vercel Skills CLI](https://github.com/vercel-labs/skills)から対象のアシスタントを選んでインストールすることもできます。

```bash
npx skills add pmwl0128/quark_drive_skills
```

全プロジェクトで使う場合は`-g`を付けます。手動の場合はリポジトリをダウンロードまたはcloneし、スキルフォルダーをコピーしたうえで、計算器用に次を実行します。

```bash
python -m pip install "quickjs-ng>=0.15"
```

導入先に`pokemon-champions-dex`、`pokemon-champions-meta`、`ncp-damage-calculator`、`pokemon-champions-team`があることを確認します。新しい会話で図鑑の質問と構築の相談をそれぞれ試し、対応するスキルが使われるか確かめてください。

更新も導入時と同じ方法で行います。`npx skills update`、`/plugin marketplace update`、または最新版の再コピー・再読み込みを使い、4つをまとめて更新してください。`.claude/skills/`は`.agents/skills/`と同じ内容です。

## 相談のしかた

やりたいことを普段の言葉で伝えてください。Showdown形式のテキストや読み取れるスクリーンショットを渡すほか、手持ちや構築を記録したローカルファイルを指定することもできます。

```text
メガメタグロスを軸に、追い風を使うダブルの構築を組んで。
team.txtの構築を、現環境の上位30匹との相性から見直して。ガオガエンは残して、入れ替える理由と失う役割も説明して。
roster.mdに載っている手持ちだけで組んで。この相手に出す4匹と先発の考え方も教えて。
```

| スキル | 主な用途 |
|---|---|
| `pokemon-champions-dex` | タイプ、種族値、特性、習得技、道具、性格、メガストーンの確認。名前の照合や「この条件に合うポケモン」の逆引きにも対応。 |
| `pokemon-champions-meta` | シングル・ダブル別の採用率、技・持ち物・相方・SP配分の傾向、過去シーズンと更新前後の比較。 |
| `ncp-damage-calculator` | ダメージの乱数、確定数、耐久ライン、素早さ関係。追い風・トリックルームなどの条件を指定して計算。 |
| `pokemon-champions-team` | 構築の作成・補完・添削、ルール適合や役割の確認、対面の検討、シングルの6匹から3匹／ダブルの6匹から4匹の選出、SP調整。必要に応じて他の3つを呼び出します。 |

継続して使う場合は、プロジェクト用のフォルダーに手持ち、構築の各版、対戦メモをまとめ、リポジトリの[AGENTS.md](AGENTS.md)または[CLAUDE.md](CLAUDE.md)をアシスタントへの指示として置いてください。実戦で得た感触や、次の変更で満たしたい条件を伝えることで、相談の意図が伝わりやすくなります。

### 提案を読むときに

データの検索、計算、ルールの確認はツールが行い、候補を選んで採用理由や欠点を説明するのはAIです。構築の相談では条件を確認し、環境と実際の構築例を調べ、候補を比較してから最終案をチェックします。データと手順に基づく確認で、数値の取り違えや習得できない技の採用、計算ミスを減らします。ただし、構築作成や診断のような複雑な相談では、提案が妥当かを実戦で確かめる必要があります。基礎能力が高く、幅広い知識を持つモデルを選ぶことも、結果の質を大きく左右します。

ときどきツールを使わず自由に回答してしまう場合は、「`pokemon-champions-team/SKILL.md`を読み、候補比較と最終確認を含めて手順どおりに進めて」と指定してください。スキルの呼び出しや長い手順への追従は、アシスタントやモデルによって差があります。

- **対象はチャンピオンズです。** 本編シリーズと種族値・習得技・仕様が異なる場合があります。チャンピオンズで未解禁の要素は扱いません。
- **ルール適合は3段階です。** `valid`は確認できた項目に合格、`invalid`は誤りあり、`unknown`は判断材料不足を表します。
- **計算の条件も確認してください。** 型、能力ランク、場の状態、仮定によって結果は変わります。KO／CHECK表は主にダメージ・素早さと限られたターン条件の評価で、積み技、回復、状態異常、長いサイクル戦のすべてを扱うものではありません。自分の構築は実際の型で計算し、標準型は確度の低い参考として扱ってください。

## ブラウザで使う

基本的な機能は**[Quark Drive Web](https://champion.mpan.top)**ですぐに試せます。環境データや図鑑の閲覧、ダメージ・素早さ計算、対面表の確認、構築の保存に対応しています。閲覧とブラウザ内の計算にアカウントは不要です。オンラインの質問、構築作成、診断、解説は、公開中の機能と利用枠に応じて使えます。構築作成はフォームから1案を返す形式で、相談を続けながら詰めたい場合はローカルのアシスタントを使ってください。

ローカル版の名称は**Quark Drive Console**です。ワークスペースや会話用のページがあり、アシスタントに作業を渡した後、質問への回答や案の承認、結果の確認をページ上で行えます。

| 機能 | オンライン | ローカル |
|---|---|---|
| 環境・図鑑・各種計算・対面表・簡易耐久計算 | 利用可 | 利用可 |
| 構築・ポケモンボックス・計算履歴 | 利用中のブラウザに保存 | ワークスペースに保存 |
| 条件を指定した厳密なSP調整 | 公開機能と処理量の制限に従う | 利用可 |
| AIとの構築相談 | オンラインの機能と利用枠 | 自分で設定したアシスタント |
| 作業の引き継ぎ・質問への回答・案の承認 | 非対応 | 対応 |

### ローカル版を起動する

[Quark_Drive_Web.zip](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/Quark_Drive_Web.zip)を展開し、そのフォルダーでターミナルを開きます。Python 3.10以上が必要です。このビルド済み配布物ではNode.jsを使いません。

Windows PowerShell：

```powershell
python -m pip install -r requirements-runtime.txt
.\start-local.ps1
```

Linux / macOS：

```bash
python3 -m pip install -r requirements-runtime.txt
chmod +x start-local.sh
./start-local.sh
```

起動時に表示される`http://127.0.0.1:<port>/#bootstrap=...`を、省略せず開いてください。リンクは一度だけ使えます。別のブラウザで開く場合や再ログインが必要な場合は、展開先で`python bridge/pcui open`を実行すると新しいリンクが出ます。ポートは`~/.pokemon-champions/pc.env`の`PCUI_LOCAL_PORT`で変更できます。

アシスタントとの連携は、ホーム画面の案内に従って設定します。`python bridge/pcui install-agent`で連携用の指示を導入でき、Codexでは`--target codex`を付けます。会話ページの引き継ぎ文をアシスタントに送り、その後の質問や確認にはページ上で回答してください。MCP対応クライアントでは`python bridge/pcui mcp`も使えます。`--print-config`を付けるとClaude Code・Codex用の登録設定を表示します。

<details>
<summary>ソースからビルドする場合</summary>

Python 3.10以上と、リリース検証で使用するNode.js 22を用意します。

```powershell
git clone https://github.com/pmwl0128/quark_drive_skills.git
Set-Location .\quark_drive_skills
python -m pip install -r frontend/requirements-ui.txt
.\frontend\build-ui.ps1
.\frontend\start-ui.ps1
```

Linux/macOSでは`frontend/build-ui.sh`と`frontend/start-ui.sh`に実行権限を付けて使います。ビルドスクリプトがnpm依存関係の導入、表示用データの生成、UIのビルドを行います。bridge、スキル、表示用データ、UIは同じ版でそろえてください。

</details>

### 保存先と更新

ローカルサービスの待受先は標準で`127.0.0.1`です。データ閲覧や計算にモデルのAPIキーは必要ありません。ワークスペースは配布版では`~/.pokemon-champions/workspace`、ソース版では起動ディレクトリ内の`storage`に保存されます。「ストレージ」ページから移動でき、バックアップはサービスを止めてフォルダー全体をコピーします。

オンラインのAI機能では、依頼に必要な入力をサービスへ送信します。ローカルでツールを動かす場合も、アシスタント自身の通信は利用するモデル提供元と設定に従います。

更新は新しい配布物を別のフォルダーへ展開して行ってください。ワークスペースを旧アプリのフォルダー内に置いた場合は、削除する前に移動します。ソース版はcheckoutを更新してビルド・起動スクリプトを実行します。生成物の一部だけを上書きすると、版がそろわなくなることがあります。

## データの更新日と配布資料

通常のスキル実行は同梱データをローカルで参照します。ページ上部や画面内のシーズン・ルール・更新日を確認し、新しいデータが必要な場合はパッケージを更新してください。過去シーズンのデータはExcel資料で確認できます。最新版のSkillsには、過去のルールやデータセットは同梱していません。

[data-latest](https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest)では、次のファイルを最新版に差し替えて配布しています。

- 環境表：[日本語](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_ja.xlsx)・[English](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_en.xlsx)・[中文](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_zh.xlsx)。シングル・ダブルの一覧と更新前後の変化を収録し、AIによる考察は加えていません。自分で`export-excel`を実行する場合のみ`openpyxl>=3.1`が必要です。
- 順位推移：[シングル](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/trend_single_ja.png)・[ダブル](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/trend_double_ja.png)。直近最大10回分の更新を表示し、英語・中国語版もあります。KO対象・脅威リストの数字は採用率ではなく順位です。取得日時や対象範囲も採用率とは異なる場合があります。
- 上記のローカル実行パッケージ。

### 参照元・謝辞

| 用途 | 参照元 |
|---|---|
| 環境データ | 主な参照元は[Silph Scope](https://silph-scope.com/)。[GameWith](https://gamewith.jp/)、[PokeChamp DB](https://pokechamdb.com/)、[MetaRoll](https://metaroll.app/ladder)は予備の参照元です。 |
| 図鑑・名称 | [Serebii](https://www.serebii.net/)、[52Poke](https://wiki.52poke.com/)、[PokéAPI](https://pokeapi.co/) |
| 計算 | [NCP VGC Damage Calculator](https://github.com/nerd-of-now/NCP-VGC-Damage-Calculator)のエンジンを同梱。[smogon/damage-calc](https://github.com/smogon/damage-calc)で独立に照合します。 |
| シングルの構築例 | [ポケモン徹底攻略](https://yakkun.com/)、[OP.GG](https://op.gg/)、[GameWith](https://gamewith.jp/)、[Game8](https://game8.jp/)、[POCH](https://poch.ms/en) |
| ダブルの構築例 | [Limitless](https://play.limitlesstcg.com/)、[Nimbasa City Post](https://www.nimbasacitypost.com/)、[VGCPastes / Pokepaste](https://pokepast.es/) |

資料やツールを公開している開発者、大会運営者、コミュニティの皆さんに感謝します。配布するのは整理済みの検索用データで、収集時の生キャッシュは含みません。

環境データ照会 API とチームコード照会にご協力いただいた [Silph Scope](https://silph-scope.com/) に、特に感謝いたします。

## 今後の予定

- 重複するツール呼び出しを減らし、アシスタントごとの手順のばらつきを抑える。
- 局面や相手の情報によって変わる判断を、構築の検討に反映しやすくする。
- オンラインの読み込みと応答を改善し、利用状況に応じて処理容量を調整する。

## ライセンス

本プロジェクトには[MITライセンス](LICENSE)を適用します。NCP計算器の上流ライセンスは`ncp-damage-calculator/references/upstream-LICENSE`に保持しています。帰属の詳細は[NOTICE](NOTICE)をご覧ください。

Pokémonおよびキャラクター名はNintendo / Creatures Inc. / GAME FREAK inc.の商標です。本プロジェクトは非公式のファンメイドツールで、各社との提携・承認関係はありません。
