/** UI-frame copy in three languages. Pokémon/move/item names NEVER come from here —
 * they arrive trilingual inside the DTOs (the dex is the naming authority, design §3). */
import { createContext, useContext, useCallback } from "react";

export type Lang = "zh" | "en" | "ja";

const STRINGS = {
  "brand.name": { zh: "夸克充能", en: "Quark Drive", ja: "クォークチャージ" },
  "nav.ranking": { zh: "环境", en: "Usage", ja: "環境" },
  "nav.dex": { zh: "图鉴", en: "Dex", ja: "図鑑" },
  "nav.trend": { zh: "趋势图", en: "Trend chart", ja: "推移グラフ" },
  "nav.matchup": { zh: "模拟", en: "Simulation", ja: "シミュレーション" },
  "nav.calc": { zh: "计算", en: "Calc", ja: "計算機" },
  "nav.teams": { zh: "存储", en: "Storage", ja: "ボックス" },
  "library.open": {
    zh: "我的队伍与宝可梦箱", en: "My teams and Pokémon Box", ja: "マイチームとボックス" },
  "library.entry": { zh: "队伍盒子", en: "Teams & Box", ja: "チームとボックス" },
  "library.discoveryTitle": { zh: "新功能 · 队伍与箱子", en: "New · Teams & Box", ja: "新機能 · チームとボックス" },
  "library.discoveryBody": {
    zh: "随时打开队伍与宝可梦箱；导入和编排请到存储页。",
    en: "Access your teams and Pokémon Box anytime. Import and organize on the Storage page.",
    ja: "どのページからもチームとボックスを開けます。インポートや編成はボックスページで。" },
  "library.dismissHint": { zh: "关闭新功能提示", en: "Dismiss feature hint", ja: "新機能の案内を閉じる" },
  "footer.community": { zh: "交流与反馈", en: "Community & feedback", ja: "交流・フィードバック" },
  "footer.qqGroup": { zh: "QQ群", en: "QQ group", ja: "QQグループ" },
  "footer.qqScan": { zh: "扫码加入 QQ 群", en: "Scan to join the QQ group", ja: "QRコードでQQグループに参加" },
  "footer.qqImageAlt": { zh: "宝可梦冠军 Skill QQ 群二维码，群号 857634818", en: "Pokémon Champions Skill QQ group QR code, group 857634818", ja: "宝可梦冠军 Skill QQグループのQRコード、グループ番号857634818" },
  "footer.qqCopy": { zh: "复制群号", en: "Copy group ID", ja: "番号をコピー" },
  "footer.qqCopied": { zh: "已复制群号 857634818", en: "Copied QQ group ID 857634818", ja: "グループ番号857634818をコピーしました" },
  "footer.copyFailed": { zh: "复制失败，请手动复制群号。", en: "Could not copy. Please copy the group ID manually.", ja: "コピーできませんでした。グループ番号を手動でコピーしてください。" },
  "footer.disclaimer": {
    zh: "Pokémon 及所有相关名称、角色、图像和内容的商标及其他知识产权归 Nintendo、Creatures Inc.、GAME FREAK inc.、The Pokémon Company 及相应权利方所有。Quark Drive 是基于可获取的游戏数据构建、可免费使用的非官方爱好者项目，与上述权利方没有关联，亦未获其认可或赞助。所有游戏内容均归其权利方所有。",
    en: "Pokémon and all related names, characters, imagery, and content are trademarks or other intellectual property of Nintendo, Creatures Inc., GAME FREAK inc., The Pokémon Company, and their respective owners. Quark Drive is an unofficial, fan-made tool, not affiliated with, endorsed, or sponsored by any of them, and is a free-to-use project built on available game data. All game content belongs to its owners.",
    ja: "Pokémon および関連するすべての名称・キャラクター・画像・コンテンツの商標およびその他の知的財産権は、Nintendo、Creatures Inc.、GAME FREAK inc.、The Pokémon Company および各権利者に帰属します。Quark Drive は入手可能なゲームデータをもとに制作された、無料で利用できる非公式のファンプロジェクトです。これらの権利者とは関係がなく、公認や後援も受けていません。ゲームに関するすべてのコンテンツは各権利者に帰属します。" },
  "library.filled": { zh: "已完成：{label}", en: "Done: {label}", ja: "完了：{label}" },
  "library.kept": { zh: "已存入「{box}」：{name}", en: "Kept in “{box}”: {name}", ja: "「{box}」に保存しました：{name}" },
  "library.full": { zh: "箱子都满了", en: "Every box is full", ja: "ボックスがすべていっぱいです" },
  "library.failed": {
    zh: "没有保存成功，请检查配置与浏览器存储后重试", en: "Could not save. Check the build and browser storage, then retry",
    ja: "保存できませんでした。型とブラウザのストレージを確認してください" },
  "library.undo": { zh: "撤销", en: "Undo", ja: "元に戻す" },
  "lib.box.name": { zh: "箱子 {n}", en: "Box {n}", ja: "ボックス {n}" },
  "lib.box.defaultSingle": { zh: "单打", en: "Singles", ja: "シングル" },
  "lib.box.defaultDouble": { zh: "双打", en: "Doubles", ja: "ダブル" },
  "format.single": { zh: "单打", en: "Singles", ja: "シングル" },
  "format.double": { zh: "双打", en: "Doubles", ja: "ダブル" },
  "ranking.title": { zh: "使用率排名", en: "Usage ranking", ja: "使用率ランキング" },
  "ranking.views": { zh: "视图", en: "View", ja: "表示" },
  "ranking.view.list": { zh: "排名", en: "Ranking", ja: "ランキング" },
  "trend.title": { zh: "排名趋势", en: "Rank trend", ja: "順位トレンド" },
  // --- meta rails ---
  "rail.search": { zh: "搜索 · 筛选", en: "Search · filter", ja: "検索・絞り込み" },
  "rail.filter": { zh: "筛选", en: "Filter", ja: "絞り込み" },
  "rail.openFilter": { zh: "打开筛选", en: "Open filter", ja: "絞り込みを開く" },
  "rail.closeFilter": { zh: "关闭筛选", en: "Close filter", ja: "絞り込みを閉じる" },
  "rail.matching": { zh: "匹配 {n} 只", en: "{n} matching", ja: "{n} 件一致" },
  "rail.close": { zh: "关闭", en: "Close", ja: "閉じる" },
  "rail.searchPlaceholder": { zh: "按名称搜索…", en: "Search by name…", ja: "名前で検索…" },
  "rail.noMatch": { zh: "无匹配", en: "no match", ja: "一致なし" },
  "rail.total": { zh: "共 {n} 只", en: "{n} total", ja: "全 {n} 匹" },
  "rail.clear": { zh: "清除", en: "Clear", ja: "クリア" },
  "rail.facetItems": { zh: "道具", en: "Items", ja: "もちもの" },
  "rail.facetMoves": { zh: "招式", en: "Moves", ja: "わざ" },
  "rail.facetAbilities": { zh: "特性", en: "Abilities", ja: "とくせい" },
  "rail.facetNatures": { zh: "性格", en: "Natures", ja: "せいかく" },
  "rail.facetPartners": { zh: "队友", en: "Teammates", ja: "相方" },
  "rail.facetKoTargets": { zh: "常击倒", en: "Most KOs", ja: "よく倒す" },
  "rail.facetKoedBy": { zh: "常被击倒于", en: "Most KO'd by", ja: "よく倒される" },
  "rail.facetSearch": { zh: "搜索…", en: "Search…", ja: "検索…" },
  "rail.loading": { zh: "载入中…", en: "loading…", ja: "読み込み中…" },
  "img.baseFallback": { zh: "显示为基础形态", en: "Showing base form", ja: "基本フォルム表示" },
  "state.loading": { zh: "加载中…", en: "Loading…", ja: "読み込み中…" },
  "state.error": { zh: "加载失败", en: "Failed to load", ja: "読み込み失敗" },
  "state.errorDetail": { zh: "加载失败，请稍后重试。", en: "Failed to load. Please try again.",
                         ja: "読み込めませんでした。しばらくしてから再度お試しください。" },
  "state.pageCrashTitle": { zh: "页面加载中断", en: "Page loading stopped", ja: "ページの読み込みが中断されました" },
  "state.pageCrashBody": {
    zh: "页面资源可能已更新，或连接暂时中断。请刷新后重试；若再次出现，请查看浏览器控制台报错。",
    en: "The page assets may have changed, or the connection was interrupted. Refresh and try again; check the browser console if it persists.",
    ja: "ページの更新または接続の中断が考えられます。再読み込みしても続く場合はブラウザーのコンソールをご確認ください。" },
  "state.reload": { zh: "刷新页面", en: "Reload page", ja: "ページを再読み込み" },
  "incompat.title": { zh: "版本不兼容", en: "Incompatible versions", ja: "バージョン不整合" },
  "incompat.body": { zh: "该页面与后端的协议版本不匹配，请更新后重试。",
                     en: "This page and the backend speak different protocol versions. Update and retry.",
                     ja: "ページとバックエンドのプロトコルが一致しません。更新して再試行してください。" },
  "auth.expired": { zh: "启动链接已被使用或已过期。在终端运行 pcui open 获取新的登录地址，无需重启服务。",
                    en: "This launch link was already used or has expired. Run pcui open in a terminal for a fresh sign-in link; the service keeps running.",
                    ja: "起動リンクは使用済みか期限切れです。ターミナルで pcui open を実行すると、サービスを再起動せずに新しいログインURLを取得できます。" },
  "runtime.error": { zh: "应用启动失败，请刷新页面后重试。", en: "The app failed to start. Refresh the page and try again.",
                     ja: "アプリを起動できませんでした。ページを再読み込みしてお試しください。" },
  "route.missingTitle": { zh: "页面不可用", en: "Page unavailable", ja: "ページを利用できません" },
  "route.missingBody": {
    zh: "该地址不存在，或当前部署没有启用这项功能。",
    en: "This address does not exist, or this deployment does not enable the feature.",
    ja: "このアドレスは存在しないか、現在の構成では機能が有効になっていません。" },
  "route.home": { zh: "返回首页", en: "Back to home", ja: "トップへ戻る" },
  // --- home (pages/HomePage.tsx): rendered by the boot shell, so it stays in this table ---
  "nav.home": { zh: "夸克充能首页", en: "Quark Drive home", ja: "クォークチャージ ホーム" },
  // Display copy (pages/home/Phrases.tsx): "|" marks where a line may break, "[…]" an emphasised run.
  // The two title parts are one heading, read as "brand · product".
  "home.title.brand": { zh: "夸克|充能", en: "Quark |Drive", ja: "クォーク|チャージ" },
  "home.title.product": { zh: "宝可梦冠军|Skills", en: "Pokémon Champions |Skills", ja: "ポケモンチャンピオンズ|Skills" },
  "home.title.accent": { zh: "[实战数据]驱动，|[AI\u00a0协同决策]的|对战引擎", en: "A battle engine |powered by [real\u2011team data] |and [AI\u2011assisted decision\u2011making]", ja: "[実戦データ]と|[AIの判断支援]を|組み合わせた、|対戦エンジン" },
  "home.cta.install": { zh: "本地安装", en: "Install locally", ja: "ローカルに導入" },
  "home.lab.title": { zh: "对战计算", en: "Battle calculations", ja: "対戦の|計算ツール" },
  "home.lab.matrix.title": { zh: "环境对位", en: "Metagame matchups", ja: "環境との相性" },
  "home.lab.matrix.body": { zh: "查看环境中实战配置的击倒关系，也可用自己的队伍进行对照。", en: "Explore KO matchups between real-team sets in the metagame, or compare your own team against them.", ja: "環境の実戦構築の型どうしで確定数を比較。自分のチームと環境の型との相性も確認できます。" },
  "home.lab.matrix.ko": { zh: "看击杀表", en: "KO table", ja: "確定数表" },
  "home.lab.matrix.actual": { zh: "分析我的队伍", en: "Check my team", ja: "自分のチームで確認" },
  "home.lab.tune.title": { zh: "耐久调整", en: "Bulk tuning", ja: "耐久調整" },
  "home.lab.tune.body": { zh: "选定要承受的对方招式，计算所需的耐久 SP 配分。", en: "Choose the opposing moves you want to survive and calculate the SP spread you need.", ja: "耐えたい相手の技を選び、必要な耐久SP配分を計算します。" },
  "home.lab.infer.title": { zh: "配置反推", en: "Set inference", ja: "型の推定" },
  "home.lab.infer.body": { zh: "输入实战中观察到的伤害，逐步缩小对方配置的可能范围。", en: "Enter the damage observed in battle to narrow down the opponent’s possible sets.", ja: "実戦で受けたダメージを入力し、相手の型の候補を絞り込みます。" },
  "home.install.title": { zh: "本地 Agent |完整体验", en: "The full |local\u2011agent experience", ja: "ローカルエージェントで|完全体験" },
  "home.install.body": { zh: "四个 Skill 在你的 AI 助手中协同工作：查图鉴、看环境、算伤害，以及结合规则、实战数据和精确计算完成建队和诊断流程。支持 Claude Code、Codex、WorkBuddy 等兼容 Agent Skills 的 AI 助手。", en: "Four skills work together in your AI assistant: look up dex facts, explore the metagame, calculate damage, and complete team-building and diagnosis workflows using rules, real-team data and exact calculations. Works with Claude Code, Codex, WorkBuddy and other assistants that support Agent Skills.", ja: "4つのスキルがAIアシスタント内で連携し、図鑑の検索・環境の確認・ダメージ計算に加え、ルール・実戦データ・正確な計算をもとにチーム構築と診断の全工程を行います。Claude Code、Codex、WorkBuddyなど、Agent Skills対応のAIアシスタントで利用できます。" },
  "home.install.col.online": { zh: "在线使用", en: "On this site", ja: "このサイト" },
  "home.install.col.local": { zh: "本地 Agent", en: "Local agent", ja: "ローカルエージェント" },
  "home.install.online.scope": { zh: "各项工具分页面使用", en: "Tools on separate pages", ja: "各ツールを専用ページで利用" },
  "home.install.local.scope": { zh: "多个 Skill 在同一段对话中协作", en: "Skills work together in one conversation", ja: "複数のスキルが同じ会話で連携" },
  "home.install.online.build": { zh: "填写表单，每次生成一支候选队伍", en: "Fill in a form; get one team candidate per run", ja: "フォームを入力し、1回につき1チームを提案" },
  "home.install.local.build": { zh: "确认需求、比较候选、反复调整队伍", en: "Clarify requirements, compare candidates and refine the team", ja: "要望を確認し、候補を比較しながらチームを調整" },
  "home.install.online.model": { zh: "轻量模型，AI 功能有每日额度", en: "A lightweight model with daily AI limits", ja: "軽量モデルを使用。AI機能には1日の利用上限あり" },
  "home.install.local.model": { zh: "不受在线额度限制，可按需换用更强模型", en: "Free from this site's online limits; switch to a more capable model as needed", ja: "このサイトのオンライン利用枠に縛られず、必要に応じて高性能なモデルに切り替え" },
  "home.install.online.files": { zh: "在网页中查看和保存结果", en: "View and save results in the browser", ja: "ブラウザで結果を確認・保存" },
  "home.install.local.files": { zh: "持久化管理本地项目文件", en: "Keeps and manages your project files locally", ja: "ローカルのプロジェクトファイルを永続的に管理" },
  "home.install.tab.agent": { zh: "让 AI 安装", en: "Ask your AI assistant", ja: "AIにインストールを依頼" },
  "home.install.tab.plugin": { zh: "Claude 插件", en: "Claude plugin", ja: "Claudeプラグイン" },
  "home.install.tab.cli": { zh: "命令行", en: "Command line", ja: "コマンドライン" },
  "home.install.agentPrompt": { zh: "全局安装 https://github.com/pmwl0128/quark_drive_skills 中的四个 Skill，并安装依赖 quickjs-ng>=0.15。如果无法安装该依赖，请确认 Node.js 回退可用。最后确认四个 SKILL.md 均能被 AI 助手发现，并实际运行一次伤害计算 Skill 的 schema 命令。", en: "Install all four skills from https://github.com/pmwl0128/quark_drive_skills globally, along with quickjs-ng>=0.15. If that dependency cannot be installed, verify the Node.js fallback. Then confirm that the AI assistant can discover all four SKILL.md files and run the damage calculator skill’s schema command.", ja: "https://github.com/pmwl0128/quark_drive_skills の4つのスキルと、依存パッケージ quickjs-ng>=0.15 をグローバルにインストールしてください。依存パッケージを導入できない場合は、Node.jsで代わりに実行できることを確認してください。最後に、AIアシスタントが4つの SKILL.md を認識できることを確認し、ダメージ計算スキルの schema コマンドを実行してください。" },
  "home.install.hint.agent": { zh: "复制给能执行命令的 AI 助手即可。", en: "Paste this into an AI assistant with shell access.", ja: "コマンドを実行できるAIに貼り付けてください。" },
  "home.install.hint.plugin": { zh: "在 Claude Code 中依次输入。", en: "Enter these in Claude Code, in order.", ja: "Claude Codeで順に入力してください。" },
  "home.install.hint.cli": { zh: "需要 Node.js；去掉 -g 可只安装到当前项目。", en: "Requires Node.js. Omit -g to install in this project only.", ja: "Node.jsが必要です。-gを外すと現在のプロジェクトだけに導入します。" },
  "home.install.copy": { zh: "复制", en: "Copy", ja: "コピー" },
  "home.install.copied": { zh: "已复制", en: "Copied", ja: "コピーしました" },
  "home.install.repo": { zh: "GitHub 仓库", en: "GitHub repository", ja: "GitHub リポジトリ" },
  "home.install.mirror": { zh: "Gitee 镜像", en: "Gitee mirror", ja: "Gitee ミラー" },
  "home.install.ui": { zh: "下载本地界面", en: "Download the local interface", ja: "ローカルUIを入手" },
  "home.install.uiNote": { zh: "本地界面可单独安装，配合本地 AI 助手查看建队进度和结果。", en: "Install the local interface to follow your AI assistant’s team-building progress and view its results.", ja: "ローカルUIをインストールすると、AIアシスタントの構築作業の進捗や結果を確認できます。" },
  "home.diag.body": { zh: "检查队伍合法性、成员分工和环境对位，可选 AI 解读", en: "Check team legality, member roles and metagame matchups, with optional AI analysis", ja: "チームのルール適合性・役割・環境との相性を確認。AIによる解説も選べます" },
  "home.diag.placeholder": { zh: "输入一个公开队伍码、粘贴 Showdown，或每行写一只宝可梦", en: "Enter one public team code, paste Showdown, or one Pokémon per line", ja: "公開コードを1つ、Showdown形式、または1行に1匹を入力" },
  "home.diag.sheet": { zh: "队伍预览", en: "Team preview", ja: "チームプレビュー" },
  "home.diag.read": { zh: "已读取 {n} / 6 只", en: "{n} / 6 slots read", ja: "{n} / 6 枠を読み取り" },
  "home.diag.unread": { zh: "到诊断页继续检查", en: "Continue on the diagnosis page", ja: "診断ページで続きを確認" },
  "home.diag.extra": { zh: "超出 {n} 只，队伍最多 6 只", en: "{n} extra Pokémon; teams hold at most 6", ja: "6匹までです（{n}匹超過）" },
  "home.diag.go": { zh: "继续诊断", en: "Continue to diagnosis", ja: "診断へ進む" },
  "home.diag.empty": { zh: "打开诊断页", en: "Open diagnosis", ja: "診断ページを開く" },
  "home.diag.library": { zh: "选择已存队伍", en: "Saved teams", ja: "保存したチーム" },
  "home.build.body": { zh: "选择赛制、打法和核心，AI 结合实战数据生成一支候选队伍。", en: "Choose a format, playstyle and core Pokémon. The AI uses real-team data to propose one team candidate.", ja: "対戦形式・方針・軸を指定すると、AIが実戦データをもとにチーム候補を1つ提案します。" },
  "home.build.format": { zh: "赛制", en: "Format", ja: "形式" },
  "home.build.core": { zh: "核心", en: "Core Pokémon", ja: "軸のポケモン" },
  "home.build.coreHint": { zh: "想围绕的宝可梦，可留空", en: "Pokémon to build around (optional)", ja: "軸にしたいポケモン（空欄可）" },
  "home.build.style": { zh: "打法", en: "Playstyle", ja: "構築方針" },
  "home.build.offense": { zh: "进攻", en: "Offense", ja: "攻め寄り" },
  "home.build.balance": { zh: "平衡", en: "Balance", ja: "バランス" },
  "home.build.defense": { zh: "防守", en: "Defense", ja: "受け寄り" },
  "home.build.tactics": { zh: "战术偏好", en: "Preferred tactics", ja: "希望する戦術" },
  "home.tactic.weather": { zh: "天气", en: "Weather", ja: "天候" },
  "home.tactic.tailwind": { zh: "顺风", en: "Tailwind", ja: "追い風" },
  "home.tactic.trickroom": { zh: "戏法空间", en: "Trick Room", ja: "トリックルーム" },
  "home.tactic.setup": { zh: "强化", en: "Setup", ja: "積み" },
  "home.build.go": { zh: "继续组队", en: "Continue to the builder", ja: "構築へ進む" },
  "home.session.body": { zh: "在本地建队会话中比较候选，逐步调整配置。", en: "Compare team candidates and refine their sets in a local build session.", ja: "ローカルの構築セッションでチーム候補を比較し、各ポケモンの型を調整します。" },
  "home.session.go": { zh: "打开会话", en: "Open sessions", ja: "セッションを開く" },
  "home.qa.placeholder": { zh: "询问宝可梦、环境、伤害或速度，也可附上一个队伍码", en: "Ask about Pokémon, meta, damage or Speed; you can include one team code", ja: "ポケモン・環境・ダメージ・素早さについて質問。コードを1つ添付可能" },
  "home.qa.go": { zh: "继续提问", en: "Continue to Q&A", ja: "質問へ進む" },
  "home.qa.examples": { zh: "例如", en: "For example", ja: "たとえば" },
  "home.lookup.search": { zh: "查图鉴", en: "Search the dex", ja: "図鑑を検索" },
  "home.lookup.calc": { zh: "打开计算器", en: "Open calculator", ja: "計算機を開く" },
  "home.glance": { zh: "环境速览", en: "Metagame at a glance", ja: "環境を|ひと目で" },
  "home.tasks": { zh: "开始使用", en: "Get started", ja: "使ってみる" },
  "home.task.diagnose": { zh: "诊断队伍", en: "Team check", ja: "チーム診断" },
  "home.task.build": { zh: "辅助组队", en: "AI team builder", ja: "構築支援" },
  "home.task.ask": { zh: "快速问答", en: "Quick Q&A", ja: "Q&A" },
  "home.art.ours": { zh: "我方队伍", en: "Your team", ja: "自分のチーム" },
  "home.art.theirs": { zh: "对方队伍", en: "Opponent", ja: "相手のチーム" },
  "home.art.damage": { zh: "伤害范围", en: "Damage range", ja: "ダメージの範囲" },
  "home.art.speed": { zh: "出手顺序", en: "Speed order", ja: "行動順" },
  "home.art.sets": { zh: "真实队伍配置", en: "Sets from real teams", ja: "実戦構築の型" },
  "home.lab.calc.title": { zh: "伤害 · 速度线", en: "Damage · Speed tiers", ja: "ダメージ・素早さライン" },
  "home.lab.calc.body": { zh: "切换双方队伍成员，实时计算伤害与速度；支持全对位计算和实战配置导入。", en: "Switch between members of either team to calculate damage and Speed. Compare every pairing and import sets from real teams.", ja: "両チームのメンバーを切り替えて、ダメージと素早さをその場で計算。全対面の比較と実戦構築の型の読み込みにも対応します。" },
  "home.lab.calc.damage": { zh: "算伤害", en: "Damage calculator", ja: "ダメージ計算" },
  "home.lab.calc.speed": { zh: "看速度线", en: "Speed tiers", ja: "素早さライン" },
  "home.lab.tune.go": { zh: "调整 SP", en: "Tune SP", ja: "SPを調整" },
  "home.lab.infer.go": { zh: "反推配置", en: "Infer a set", ja: "型を絞り込む" },
  "home.diag.current": { zh: "使用当前队伍", en: "Use active team", ja: "選択中のチームを使う" },
  "home.diag.restore": { zh: "恢复示例", en: "Restore example", ja: "サンプルに戻す" },
  "home.diag.clear": { zh: "清空", en: "Clear", ja: "クリア" },
  "home.cta.online": { zh: "在线体验", en: "Try it online", ja: "Webで試す" },
  "home.cta.open": { zh: "开始使用", en: "Get started", ja: "はじめる" },
  "home.flow.context": { zh: "需求确认", en: "Requirements", ja: "条件確認" },
  "home.flow.frame": { zh: "队伍框架", en: "Team cores", ja: "構築の軸" },
  "home.flow.ground": { zh: "环境数据", en: "Metagame data", ja: "環境データ" },
  "home.flow.generate": { zh: "生成队伍", en: "Build candidate", ja: "候補を生成" },
  "home.flow.evaluate": { zh: "对位检查", en: "Matchup checks", ja: "対面チェック" },
  "home.flow.audit": { zh: "结果复核", en: "Final review", ja: "最終確認" },
  "home.flow.context.d": { zh: "确认需求，补齐未指定条件", en: "Check requirements and fill in missing defaults", ja: "条件を確認し、未指定の項目を補う" },
  "home.flow.frame.d": { zh: "从实战队伍提取参考核心", en: "Find reference cores in real-team data", ja: "実戦構築から参考になる軸を抽出" },
  "home.flow.ground.d": { zh: "读取排名与候选的常见配置", en: "Read usage rankings and common candidate sets", ja: "ランキングと候補のよくある型を確認" },
  "home.flow.generate.d": { zh: "AI 选择六只宝可梦并补全配置", en: "AI selects six Pokémon and fills in their sets", ja: "AIが6匹を選び、それぞれの型を組み立てる" },
  "home.flow.evaluate.d": { zh: "检查合法性与环境对位风险", en: "Check legality and metagame matchup risks", ja: "ルール適合性と環境での対面リスクを確認" },
  "home.flow.audit.d": { zh: "复核结论依据、前提与取舍", en: "Review evidence, assumptions and trade-offs", ja: "結論の根拠・前提・選択理由を再確認" },
  "home.flow.in": { zh: "你的要求", en: "Your brief", ja: "あなたの要望" },
  "home.flow.out": { zh: "推荐队伍", en: "Recommended team", ja: "おすすめのチーム" },
  "home.qa.body": { zh: "用自然语言提问，AI 查询相关数据并组织回答。", en: "Ask in plain language. The AI looks up relevant data and explains the answer.", ja: "ふだんの言葉で質問すると、AIが関連データを調べて回答します。" },
  "home.qa.sources": { zh: "数据与工具", en: "Data and tools", ja: "参照するデータとツール" },
  "home.qa.src.dex": { zh: "图鉴", en: "Dex", ja: "図鑑" },
  "home.qa.src.find": { zh: "条件反查", en: "Search by criteria", ja: "条件検索" },
  "home.qa.src.ranking": { zh: "使用率排名", en: "Usage ranking", ja: "採用率ランキング" },
  "home.qa.src.sets": { zh: "常见配置", en: "Common sets", ja: "よくある型" },
  "home.qa.src.real": { zh: "真实队伍配置", en: "Real-team sets", ja: "実戦構築の型" },
  "home.qa.src.speed": { zh: "速度线", en: "Speed tiers", ja: "素早さライン" },
  "home.qa.src.damage": { zh: "伤害计算", en: "Damage calcs", ja: "ダメージ計算" },
  "home.art.immune": { zh: "无效", en: "No effect", ja: "効果なし" },
  "home.art.all": { zh: "全对位结果", en: "All matchups", ja: "全対面の結果" },
  "home.pop.rank": { zh: "环境第 {n} 名", en: "Usage rank #{n}", ja: "採用率 {n} 位" },
  "home.pop.vs": { zh: "对", en: "vs", ja: "対" },
  "home.pop.deals": { zh: "输出", en: "Deals", ja: "与ダメ" },
  "home.pop.takes": { zh: "承伤", en: "Takes", ja: "被ダメ" },
  "home.pop.stab": { zh: "本系", en: "STAB", ja: "タイプ一致" },
  "home.pop.first": { zh: "先手", en: "Moves first", ja: "先手" },
  "home.pop.second": { zh: "后手", en: "Moves second", ja: "後手" },
  "home.pop.tie": { zh: "同速", en: "Speed tie", ja: "同速" },
  "qa.ex1": { zh: "双打中美纳斯的真实队伍代表配置，和各项配置的环境最高使用率相比有哪些异同？",
              en: "In doubles, how does Milotic’s representative real-team set compare with the most-used option for each field — what matches and what differs?",
              ja: "ダブルのミロカロスの実戦代表型は、各項目の環境最多採用と比べてどこが同じでどこが違う？" },
  "qa.ex2": { zh: "超级耿鬼的速度范围是多少？按常见配置，哪些环境对手能稳定比它快？", en: "What is Mega Gengar’s Speed range? With common sets, which metagame opponents reliably outspeed it?", ja: "メガゲンガーの素早さはどの範囲？よく使われる型では、どの環境ポケモンに確実に抜かれる？" },
  "qa.ex3": { zh: "按单打实战代表配置，超级巨金怪的子弹拳能确一阿罗拉九尾吗？请说明配置假设。", en: "With representative real-team sets in singles, can Mega Metagross guarantee an OHKO on Alolan Ninetales with Bullet Punch? State the set assumptions.", ja: "シングルの実戦代表型で、メガメタグロスのバレットパンチはアローラキュウコンを確定1発にできる？型の前提も示して。" },
  "a11y.language": { zh: "界面语言", en: "Interface language", ja: "表示言語" },
  "theme.selector": { zh: "显示模式", en: "Display mode", ja: "表示モード" },
  "theme.toDark": { zh: "切换到夜间模式", en: "Switch to dark mode", ja: "ダークモードに切り替え" },
  "theme.toLight": { zh: "切换到日间模式", en: "Switch to light mode", ja: "ライトモードに切り替え" },
  "theme.toAuto": { zh: "自动（跟随系统）", en: "Auto (follow system)", ja: "自動（システム設定に合わせる）" },
  "a11y.skipContent": { zh: "跳至主要内容", en: "Skip to main content", ja: "メインコンテンツへ移動" },
  "a11y.primaryNav": { zh: "主要导航", en: "Primary navigation", ja: "メインナビゲーション" },
  "a11y.format": { zh: "对战赛制", en: "Battle format", ja: "対戦形式" },
  "nav.sessions": { zh: "会话", en: "Sessions", ja: "セッション" },
  "nav.assistant": { zh: "辅助", en: "Assist", ja: "サポート" },
} as const satisfies Record<string, Record<Lang, string>>;

/** Effect/description text for the interface language; English is the permanent
 * fallback (design §2.1 — never hand-translate). */
export function effectText(e: { effect?: string; effectZh?: string; effectJa?: string },
                           lang: Lang): string {
  if (lang === "zh") return e.effectZh ?? e.effect ?? "";
  if (lang === "ja") return e.effectJa ?? e.effect ?? "";
  return e.effect ?? "";
}

export type MsgKey = keyof typeof STRINGS;

/** Dynamic-key lookup for vocab-shaped keys (`gapfield.<field>`, `frame.mode.<mode>`, …):
 * returns the key when a translation exists, null so callers fall back to the raw token. */
export function optionalKey(key: string): MsgKey | null {
  return key in STRINGS ? (key as MsgKey) : null;
}

export const LangContext = createContext<{ lang: Lang; setLang: (l: Lang) => void }>({
  lang: "zh",
  setLang: () => {},
});

export function useLang() {
  return useContext(LangContext);
}

export function useT() {
  const { lang } = useContext(LangContext);
  return useCallback((key: MsgKey): string => STRINGS[key][lang], [lang]);
}

type Messages = Record<string, { zh: string; en: string; ja: string }>;

/** Every key a route-scoped translator accepts: its own plus the entry table's. */
export type ScopedKey<M extends Messages> = (keyof M & string) | MsgKey;

/** Route-scoped copy. A page's own strings live in its lazy chunk instead of this entry table:
 * `const useLibraryT = defineMessages(LIBRARY_MESSAGES)`, then `useLibraryT()` translates the page's
 * keys and still falls through to every shared key here. `useLibraryT.optionalKey` is the scoped
 * twin of `optionalKey`: a dynamic key that lives in the route's table is found there, where the
 * entry-only lookup would quietly fall back to the raw token. */
export function defineMessages<M extends Messages>(messages: M) {
  function useScopedT() {
    const { lang } = useContext(LangContext);
    return useCallback((key: ScopedKey<M>): string =>
      key in messages ? messages[key]![lang] : STRINGS[key as MsgKey][lang], [lang]);
  }
  useScopedT.optionalKey = (key: string): ScopedKey<M> | null =>
    key in messages || key in STRINGS ? key as ScopedKey<M> : null;
  return useScopedT;
}

/** Pick the display name for the current language from a trilingual DTO (English canonical
 * `name` is the guaranteed fallback). */
export function displayName(
  d: { name: string; nameZh?: string; nameJa?: string }, lang: Lang): string {
  if (lang === "zh") return d.nameZh ?? d.name;
  if (lang === "ja") return d.nameJa ?? d.name;
  return d.name;
}

/** Secondary line: the English canonical when the primary is localized. */
export function secondaryName(
  d: { name: string; nameZh?: string; nameJa?: string }, lang: Lang): string | null {
  const primary = displayName(d, lang);
  return primary === d.name ? null : d.name;
}

/** Read the saved language. Accessing localStorage THROWS (not just returns null) in browsers
 * with site data blocked or in a sandboxed iframe — swallow it so the app falls back to
 * navigator.language instead of white-screening on first render. */
function readSavedLang(): Lang | null {
  try {
    const saved = localStorage.getItem("pc-lang");
    return saved === "zh" || saved === "en" || saved === "ja" ? saved : null;
  } catch {
    return null;
  }
}

export function saveLang(lang: Lang): void {
  try {
    localStorage.setItem("pc-lang", lang);
  } catch { /* storage blocked — language just won't persist across reloads */ }
}

export function detectLang(): Lang {
  const saved = readSavedLang();
  if (saved) return saved;
  const nav = navigator.language.toLowerCase();
  if (nav.startsWith("zh")) return "zh";
  if (nav.startsWith("ja")) return "ja";
  return "en";
}
