<h1 align="center">Quark Drive Skills</h1>

<p align="center">夸克充能 · クォークチャージ · Quark Drive</p>

<p align="center">
  <b>帮助 AI 助手用本地事实、当前环境数据和精确计算查询图鉴、计算对位、诊断队伍并完成建队</b>
</p>


<p align="center">
  <img alt="skills" src="https://img.shields.io/badge/skills-4-blue"/>
  <img alt="modes" src="https://img.shields.io/badge/single%20%26%20double-supported-success"/>
  <img alt="query" src="https://img.shields.io/badge/query-offline--first-success"/>
  <img alt="languages" src="https://img.shields.io/badge/names-zh%20%7C%20en%20%7C%20ja-orange"/>
  <img alt="agents" src="https://img.shields.io/badge/Claude%20Code%20%7C%20Codex-Agent%20Skills-blueviolet"/>
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green"/>
</p>

<p align="center">
  <b>环境快照</b> · M-7 / M-C · 截至 <b>2026-10-18</b>
</p>

<p align="center">
  <a href="README.md">English</a> | <b>中文</b> | <a href="README_ja.md">日本語</a>
</p>

---

Quark Drive Skills 是供 AI 助手使用的《宝可梦冠军》本地技能集合，包含四个可独立调用的 Agent Skill。配套浏览器界面称为 **Quark Drive Web**，两者在同一仓库交付。技能支持：

- 查确定事实的对战图鉴；
- 查询当前或历史环境分布；
- 计算具体伤害、击杀、生存和速度线；
- 以完整审计流程建队、改队、诊断和调校。

四个技能会并列安装和调用。`pokemon-champions-team` 处理整队任务时会协调另外三个事实技能；单独查询图鉴、环境或一次计算时，AI 助手仍会直接使用对应技能。

所有查询默认读取随包发布的本地数据。系统的目标不是替 AI 制造一个“队伍强度分”，而是让它的事实、计算、假设和取舍可以被检查。

## 项目定位

| 它是什么 | 它不是什么 |
|---|---|
| 一套安装到 Claude Code、Codex 等 AI 助手中的本地技能 | 必须注册网页账号或打开可视化界面才能使用的服务 |
| 图鉴、环境、计算和真实队样本的结构化查询层 | 依靠模型记忆回答的百科聊天机器人 |
| 带规则校验、证据、置信度和审计步骤的队伍辅助流程 | 自动产出“客观最强队”的黑箱优化器 |
| 当前赛季/规则的可更新快照 | 永远实时、无需关注日期的线上数据库 |

脚本负责事实、计算和确定性检查；AI 负责理解你的目标、设计候选、权衡利弊并解释最终建议。这个分工是整套项目最重要的边界。

### Quark Drive Web：可选浏览器界面

四个技能可通过支持工具调用的 AI 助手完整使用。Quark Drive Web 提供浏览器工作台，方便查看图鉴、环境、配置与建队进度，并进行伤害、速度、对位、耐久调校和队伍诊断：

- **在线使用：**打开 [champion.mpan.top](https://champion.mpan.top)，无需安装。注意在线端因资源限制，功能与本地版相比有所简化。
- **本地使用：**从 [`data-latest` Release](https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest) 下载 [`Quark_Drive_Web.zip`](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/Quark_Drive_Web.zip)。预构建包需要 Python 3.10+ 和包内运行时依赖；普通浏览与计算不需要 Node.js、账号或模型 API Key。

界面不是技能的前置依赖，也不会代替 AI 助手完成完整的本地协作流程。功能差异、安装依赖和使用方法见[Quark Drive Web](#quark-drive-web)。

> [!NOTE]
> **数据范围仅限《宝可梦冠军》。**
>
> 四个技能对外支持的数据范围只限《宝可梦冠军》当前正式版本中已经上线的宝可梦、形态、招式、道具、特性与规则；可查询的历史 season 也只是《宝可梦冠军》自身的环境快照。这里不包含《宝可梦 剑／盾》《宝可梦 朱／紫》等正作系列的数据，也不收录尚未正式上线的内容。同名宝可梦或招式在正作与《宝可梦冠军》中可能有种族值、威力、特性、学习集或机制差异，所有查询与计算均以《宝可梦冠军》的现行数据为准。

## 技能一览

| Skill | 最适合回答 |
|---|---|
| [`pokemon-champions-dex`](#pokemon-champions-dex) | “它是什么、会什么、这个名字对应谁？” |
| [`pokemon-champions-meta`](#pokemon-champions-meta) | “现在什么常见、通常怎么用、这期变了什么？” |
| [`ncp-damage-calculator`](#ncp-damage-calculator) | “这一击多少、能不能确一/扛住、谁更快？” |
| [`pokemon-champions-team`](#pokemon-champions-team) | “帮我组队、检查这队、怎么改、怎么选、怎么调 SP？” |

## `pokemon-champions-dex`

离线对战图鉴，负责全项目的名称归一和 Champions 规则事实。它查询宝可梦与形态、属性、种族值、特性、学习集、招式、道具、性格、Mega 石对应关系，并支持批量查询和多条件反查。

**它擅长：**

- 中文、英文、日文名称互认，包括常见别名和多语言 Mega 写法；
- 批量解析用户给出的队伍、持有列表和配招名称；
- 反查“谁会某招”“同时满足哪些属性/特性/速度条件”；
- 为 meta、calculator 和 team 提供统一的英文规范名。

**它的边界：**图鉴命中只说明“存在、合法可学或具备该事实”，不代表环境常见，也不代表对战上值得采用。

可以直接问：

```text
烈咬陆鲨能学冰冻拳吗？
哪些妖精属性宝可梦会大地之力？
X喷的种族值和特性是？
目前宝可梦冠军的合法道具中包括突击背心吗？
```

## `pokemon-champions-meta`

离线优先的环境快照与查询 API，负责“当前环境里实际出现了什么”。它按 season、rule 和单打/双打分别提供使用率排名，以及招式、道具、特性、性格、队友和 SP 分布等详情面板。

**它擅长：**

- 单打或双打前排环境概览；
- 查询某只宝可梦的常见配置与搭档；
- 反查使用某招、某道具或某类配置的常见宝可梦；
- 比较单打与双打差异；
- 读取相对上一快照的事实变动报告。

**它的边界：**详情面板里的招式、道具、特性、性格和队友通常是各自独立的边际分布。不能把每一列第一名直接拼成一套“常见完整配置”，更不能把使用率当作强度证明。

可以直接问：

```text
当前双打使用率前 20 是谁？
Mega 巨金怪在单打和双打的常用招式有什么差别？
本期相比上一期meta数据，有哪些宝可梦明显升降，常用道具发生了什么变化？
双打里常见的顺风手都有谁？通常和谁一起出现？
```

最新的三份单语环境工作簿（`workbook_{zh,ja,en}.xlsx`，含单打、双打完整环境表和事实性更新报告）以固定文件名发布在 [data-latest Release](https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest)，随每次发行滚动覆盖；无需运行脚本即可浏览，使用率请以页首快照日期为准。

## `ncp-damage-calculator`

基于随包内置的 NCP VGC Damage Calculator 核心，提供《宝可梦冠军》伤害与速度计算。它处理具体的攻击方、防守方、招式、SP、性格、道具、特性、能力阶段和场地状态，返回完整伤害随机数、百分比、击倒／耐久结论与速度关系。

**它擅长：**

- 一次具体攻击的伤害区间与 OHKO/2HKO 线；
- 指定配置能否吃下一击；
- 两个完整速度状态的先后手比较；
- 顺风、戏法空间、围巾、天气等条件下的速度线；
- 为 team 技能的 matchup 与 tune 提供精确底层计算。

**它的边界：**计算成功不等于配置在《宝可梦冠军》中可用；它也不是完整回合模拟器，不会替你推演所有回复、状态、PP 和双方选择分支。未写明的配置与场地条件都属于假设，回答时应一并列出。

可以直接问：

```text
固执 32 攻 Mega 巨金怪的地震打这只 Mega 雷丘 Y 多少？
炽焰咆哮虎能不能保证吃下常见烈咬陆鲨的地震？
顺风下这两只谁先动？戏法空间里顺序会不会反过来？
我的这套 SP 还差多少才能过速目标？
```

## `pokemon-champions-team`

这是整队任务的专用技能：建新队、补全队伍、审队、改队、合法性校验、对位分析、选出分析和 SP 调校都由它处理。它与另外三个技能并列发布，但在一次整队任务内部会把 dex 的规则事实、meta 的环境分布和 calculator 的精确计算组织成一条可审计工作流。

它不直接从一句话生成一支看似合理的队。真正的目标是：**在不把建队变成黑箱评分器的前提下，让 AI 从用户意图、当前环境、真实联合队伍和可复算对位出发，形成透明建议。**

> [!IMPORTANT]
> **它能提高建队回答的下限，不能替代高水平建队经验。**
>
> 这套技能能减少过时记忆、名称错配、非法配置、伤害算错、把各项使用率误拼成完整配置、遗漏用户约束等常见问题；它提高的是 AI 输出的**事实下限和推理纪律**。同一套技能由不同模型驱动时，候选质量和解释深度仍会有明显差异。队伍是否真正适合你的打法、选出习惯、对手群体和临场计划，也取决于你提供了多少有价值的对战信息。

不要把第一次输出当成自动生成的最优答案。想得到更好的结果，请告诉 AI 你真正想实现的胜利方式、核心选出或首发思路、常遇到的对手、愿意接受的弱点、实战中输在哪里，以及你已经掌握的人类经验。最有效的用法通常是：先得到一份有数据依据的方案，实战或复盘后反馈具体问题，再让它继续改队和调线。

### 你可以怎样提供输入

没有指定文件名，也没有要求用户先学会某种 JSON schema。以下形式都可以：

- 直接说“围绕某只宝可梦组一队”；
- 粘贴 Showdown 风格队伍；
- 粘贴已有的 team JSON；
- 用自然语言逐只描述队员、配招和 SP；
- 指向本地任意文件中的队伍或持有列表；
- 提供一张清晰截图，让 AI 先读出内容；
- 只给限制条件，例如“只用我拥有的宝可梦”“保留这只 Mega”“不要空间”。

AI 会先用 dex 解析所有宝可梦、招式、道具、特性和性格，再把队伍事实、硬限制、软偏好和对位目标整理成可检查的结构化输入。清洗格式是 AI 助手的工作，不需要你处理。

### 能力地图

| 能力 | team 技能实际做什么 |
|---|---|
| **输入与合法性** | AI 将自然语言、截图和文件整理为结构化队伍输入；脚本解析 Showdown 或 JSON，检查名称、可用名单、学习集、特性、道具、同种／同道具限制、队伍规模、SP 与 Mega 关系。结果区分 `valid / invalid / unknown`。 |
| **队伍诊断** | 从防守属性、进攻覆盖、速度结构、速度控制和功能信号等维度列出事实与缺口，不给一个综合分。 |
| **对位与选出** | 针对当前环境前 K 名计算双方伤害、速度和 KO 关系；分析单打 6 选 3、双打 6 选 4 与每场只能进行一次 Mega 进化等选出约束，不替用户宣布“最佳选出”。 |
| **SP 调校** | 把“扛住 X、快过 Y、确一／确二 Z”变成明确的对位目标，计算跨过离散阈值所需的最小 SP 及其代价，而不是搜索一套所谓的“最优努力值”。 |
| **候选与真实队证据** | 根据缺口检索补位／替换候选，比较修改前后的客观差异，并用实战队伍的联合结构、代表配置和完整样本帮助 AI 组装自己的方案。 |
| **结果检查** | 对候选使用同一套事实检查，拦截非法或违反硬约束的方案，并检查最终回答是否遗漏假设、取舍、调校说明或无法复算的数字。 |

### 它怎样完成整队任务

team 技能内部使用一套流程化约束来减少“先凭印象生成，再事后找理由”的问题。最终用户不需要理解各个内部算子；一次完整任务可以概括为四步：

1. **理解目标**：确认格式、核心、可用范围、硬限制、玩法倾向和必须满足的对位线；开放式建队会分批提问，已有队伍分析只补真正缺失的信息。
2. **查实数据依据**：同时查看当前环境和真实联合队伍，区分“什么常见”与“哪些配置确实一起出现”，避免从各项独立使用率中拼出并不存在的配置。
3. **比较候选**：生成一个或多个方向，使用同一套合法性、结构、对位和置信度检查，说明每个选择解决了什么、又付出了什么。
4. **验证后交付**：对需要的伤害、速度、选出和 SP 线做精确计算，复核最终文字中的数字、假设与取舍，再把结果交给用户继续判断和迭代。

按流程执行可以记录依据、检查约束并复算关键数字；AI 仍须遵守各个门槛并准确说明检查结果。它不会判断某种对战哲学一定正确，也不会把一支队认证为客观最优。

### 三种常见使用路径

| 你的请求 | 它会怎样处理 |
|---|---|
| **从零建队** | “建队流程”会进入完整引导；普通建队请求会按缺失信息分批询问，再给出经过比较和验证的方向。 |
| **审核或改队** | 直接整理现有队伍，先检查合法性和结构；涉及环境时补对位，需要换人时说明修改前后的收益与代价。用户要求保留的核心不会被顺手删掉。 |
| **选出、对位、调 SP** | 按问题分别做 6 选 3/4、完整环境对位或具体阈值调校；“当前能否扛住”和“请调到能扛住”不会混为同一种计算。 |

### 真实队样本怎样被使用

发行版内置按赛季、规则和单/双分区的真实队样本。它们提供的是**联合事实**：某六只是否共同出现、某成员的道具/特性/招式/性格/SP 是否来自同一套实际配置、哪些结构在特定证据等级中反复出现。

样本库不是排行榜：

- 战绩、名次、rating 和样本数不会被归一化成强度分；
- 单打和双打不混合，不同规则不会偷偷进入当前证据池；
- 只有物种列表的样本可用于共现分析，但不能冒充完整的代表配置；
- 标准配置缓存属于低置信参考；以环境分布补充的 SP 属于估计配分，用户自己的队伍按实际配置重新计算；
- AI 可以阅读整队作为证据，但不能静默把库中某队原样回显成“推荐”；确实决定原样采用时，必须说明样本重叠事实、为什么适合，以及考虑过哪些修改。

### 怎样阅读 team 输出

**合法性不是二元猜测。** `valid` 表示已在当前可用事实上通过确定性检查；`invalid` 会给出具体错误；`unknown` 表示输入或底层事实不足，不能把“不知道”写成“合法”。

**使用率不是强度。** meta 排名、真实队共现和赛事标签只提供不同视角。AI 可以在解释取舍后推荐，但工具不会把它们合成一个不可审计的分数。

**CHECK 不是对战模拟器。** C2／C1／C0 基于指定配置、伤害、速度和回合预算。强化、回复、状态、PP 与长期消耗只会在工具能力范围内检测和提示，不能把 C2 理解为绝对克制。

**置信度也是结论的一部分。** 真实联合配置、边际分布补全、标准配置缓存、样本过少或部分计算失败，对应的置信度和注意事项各不相同。最终回答应保留关键假设。

### 可以直接这样提问

```text
建队流程：我想组一支当前规则双打队，先问我需要确认的内容。

围绕 Mega 姆克鹰做一支单打队，只要一支最终方案，但把关键取舍说清楚。

这是我的双打队伍，帮我检查合法性、结构短板和当前环境前 20 的对位，然后提出修改。

只使用 roster.txt 里我拥有的宝可梦组双打，保留耿鬼，不要戏法空间。

这六只双打通常应该怎么选四只？不同对手需要换哪条选出轴？

把我的炽焰咆哮虎调到能保证吃下指定烈咬陆鲨的地震，同时说明要牺牲什么 SP。

比较把队里 A 换成 B 前后的防守、速度控制和环境漏洞，不要只说谁更热门。

找出真实队库里包含这两只核心的结构，但不要直接复制整队，先总结共同点和变体。
```

## 四个技能怎样协作

同一个问题可以触发多个技能，但事实责任不会混淆：

```text
用户自由文本
  ├─ 名称、形态、学习集、道具是否合法 ──> dex
  ├─ 当前常见度、配置边际、搭档分布 ───> meta
  ├─ 指定条件下的伤害与速度 ──────────> calculator
  └─ 整队意图、候选、诊断、调校与审计 ─> team
                                      └─ 按需调用前三者
```

例如，“以超级巨金怪为核心组一支双打队，并保证它能承受常见生命宝珠烈咬陆鲨的地震”会用到名称与规则校验、当前环境、实战队伍结构、候选评估和伤害阈值计算。若只问“环境中常见的超级巨金怪用子弹拳能否确一常见配置的阿罗拉九尾”，则只需调用 dex、meta 和 calculator，不会进入建队流程。

## Quark Drive Web

配套前端的目标是把四个技能已经提供的事实与计算变成便于浏览、对照和复核的工作台。它适合快速查看环境、研究某只宝可梦、批量比较伤害与速度、检查标准或自定义配置的对位，以及把队伍分析结果继续送入其他页面验证；中／英／日名称只在展示边界本地化。页面右上角会显示当前 season、rule 和快照日期；涉及环境的判断应以这里的版本信息为准。

如果项目里已有可用命令行的 AI 助手，可以用一句话让它替你下载、安装并启动本地版——见下方「方法 A — 让 AI 助手代为安装」。具体步骤助手已记录在 `AGENTS.md` / `CLAUDE.md` 的「Local Visual Interface」一节。

### 功能与运行方式

| 功能 | 在线版 | 本地运行包 |
|---|---|---|
| 单打／双打排名、详情、KO 面板、分别采集的使用率／KO 历史，以及按真实队伍统计并与官方数据对照的竞技环境 | 支持 | 支持 |
| 宝可梦、招式、道具、特性图鉴与中／英／日名称检索 | 支持 | 支持 |
| 伤害批量计算、完整乱数、击倒线与速度线 | 浏览器本地计算 | 浏览器本地计算 |
| 计算页搜索筛选抽屉、真实配置、Meta 回填与空白自定义配置 | 支持 | 支持 |
| 环境标准配置对位矩阵、自定义队伍 KO／CHECK 表 | 支持 | 支持 |
| 快速耐久试算 | 支持 | 支持 |
| 我的队伍、宝可梦箱与计算记录 | 保存在当前浏览器 | 保存在本地工作区文件夹 |
| 精确 `team.tune` 耐久调校 | 按当前开放功能与服务工作量额度提供 | 支持 |
| 单次事实问答、简化建队向导、队伍诊断与可选 AI 解读 | 按在线部署和每日额度开放 | 由本地 AI 助手运行完整流程 |
| UEP 会话与 artifact 面板 | 不支持 | 支持，与正在工作的本地 AI 助手协作 |

在线建队向导是固定表单驱动的简化入口，只返回一次经过规则检查的结果，没有自由连续对话、候选来回比较或长期历史。需要完整的提问澄清、候选比较、改队迭代和本地文件协作时，仍应在安装了四个技能的 AI 助手中完成。

计算页抽屉一次展开一只宝可梦的配置卡，顶部开关决定填入哪一方。速度线双方各支持最多六只，快速耐久试算最多支持六只来袭宝可梦；达到上限时会明确提示，不能再添加。环境详情中的 KO 目标与威胁列表是**名次，不是使用率百分比**；它们的采集时间和覆盖范围可能与使用率历史不同，未采集的部分会显示为数据缺口。

### 直接使用在线版

访问 [https://champion.mpan.top](https://champion.mpan.top) 即可。环境、图鉴、趋势、对位和浏览器计算不需要账号；事实问答、建队与 AI 解读是否显示，以及可用次数，由当前在线开放的功能与额度决定。

建议按以下顺序使用：

1. 先检查页首的 season、rule 和数据日期，再选择单打或双打。
2. 从环境排名或图鉴进入宝可梦详情，查看属性、种族值、学习集与当前常见配置。
3. 将详情或队伍配置带入计算器，明确攻击方、防守方、SP、性格、道具、特性和场地后再读取伤害／速度结论。
4. 对位页的环境标准配置只适合做低置信参考；判断自己的队伍时，应粘贴或填写实际配置并重新计算。
5. 在线建队或诊断结果可以继续送入实际对位与计算器复核，不要把首次输出视为自动最优答案。

### 下载预构建本地版

预构建包把同一次发行的 SPA、公开投影、Python bridge、四个技能、运行时配置和校验清单绑定在一起。只需要 **Python 3.10+**；依赖由包内精确锁定的 `requirements-runtime.txt` 安装，**不需要 Node.js**。

#### 方法 A — 让 AI 助手代为安装（推荐）

如果项目目录里已经有可命令行调用的 AI 助手（即你用前面的方法 A 安装了技能，或已把本仓库的 `AGENTS.md` / `CLAUDE.md` 放进项目目录），直接对它说：

> 从 `pmwl0128/quark_drive_skills` 的 `data-latest` release 下载预构建的本地前端，安装 `requirements-runtime.txt`，并启动它。然后把本地访问地址给我。

助手会代为下载压缩包、解压、安装运行时依赖、启动对应平台的启动脚本，并返回一次性地址 `http://127.0.0.1:<port>/#bootstrap=...`。完整步骤它已在 `AGENTS.md` / `CLAUDE.md` 的「Local Visual Interface」一节中持有，你无需记命令。

#### 方法 B — 手动

解压缩并进入前端目录后：

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

启动成功后，终端会打印一个形如 `http://127.0.0.1:1025/#bootstrap=...` 的一次性地址。首次打开时请使用终端打印的完整地址；页面换取本地会话 cookie 后会自动移除 URL 中的 token。服务默认只监听本机回环地址，按 `Ctrl+C` 停止。

登录地址只能使用一次。需要在另一个浏览器打开、或服务重启后页面提示已断开时，在解压目录运行 `python bridge/pcui open`，即可获得新的登录地址，无需重启服务。

要让 AI 助手处理本地界面里创建的会话，先运行一次 `python bridge/pcui install-agent`（Codex 加 `--target codex`）。本地首页的「开始使用」会显示本机可直接复制的完整命令；之后把会话页里的交接说明发给助手即可，进展会实时显示在页面上；助手提出的问题和需要你决定的检查点可以直接在会话页回答，正在等待的助手收到后会自动继续。支持 MCP 的助手也可以改用 `python bridge/pcui mcp` 接入，`python bridge/pcui mcp --print-config` 会打印 Claude Code 与 Codex 的注册写法。

如需修改默认端口，在 `~/.pokemon-champions/pc.env` 中写入：

```dotenv
PCUI_LOCAL_PORT=1025
```

### 从源码构建

从源码构建适合需要检查或修改前端的用户。推荐使用项目发布验证所采用的 **Node.js 22**，另需 Python 3.10+ 和 npm。JavaScript 依赖由 `frontend/package-lock.json` 固定；`frontend/build-ui` 脚本会执行 `npm ci`、生成与当前技能数据一致的公开 projection，再运行一次 Vite build。

Windows PowerShell：

```powershell
git clone https://github.com/pmwl0128/quark_drive_skills.git
Set-Location .\quark_drive_skills
python -m pip install -r frontend/requirements-ui.txt
.\frontend\build-ui.ps1
.\frontend\start-ui.ps1
```

Linux / macOS：

```bash
git clone https://github.com/pmwl0128/quark_drive_skills.git
cd quark_drive_skills
python3 -m pip install -r frontend/requirements-ui.txt
chmod +x frontend/build-ui.sh frontend/start-ui.sh
./frontend/build-ui.sh
./frontend/start-ui.sh
```

构建生成的 `dist`、projection 与本地 bridge 必须来自同一份仓库快照。不要把旧版 `dist`、新版 projection 或另一版技能目录手工拼在一起；启动时的协议、计算引擎摘要和 deployment ID 检查会拒绝不一致的组合。

### 本地数据与在线隐私边界

- 本地运行包默认只监听 `127.0.0.1`，不需要账号或模型 API Key；安装依赖并取得发行包后，普通图鉴、环境、计算、对位、调校与会话操作读取本机数据。
- 队伍、宝可梦、计算结果与 AI 会话保存在本机工作区：预构建包默认是 `~/.pokemon-champions/workspace`，源码版默认是启动目录下的 `storage`，可在存储页更改或迁移。工作区不在解压目录内，更新时不受影响；备份时先停止本地服务，再复制整个工作区文件夹。
- 在线环境浏览与伤害／速度计算使用公开 projection 和浏览器内计算引擎；事实问答、建队、队伍诊断、自定义实际对位或 AI 解读等服务端功能会把对应输入发送到在线服务。
- 不希望提交到在线服务的私人队伍、复盘或持有信息，应留在本地运行包或本地 AI 助手项目中处理。

### 更新本地界面

`Quark_Drive_Web.zip` 使用固定文件名，在 `data-latest` Release 中随发行滚动替换。更新预构建版时，请重新下载整个压缩包并解压到新目录，确认新版本可启动后再删除旧目录；不要只覆盖其中几个子目录。工作区在发行目录之外，删除旧目录不会删除你的资料；若曾把工作区放在旧发行目录里，请先在存储页迁移。

源码版更新后运行：

```bash
git pull
```

然后重新执行 `frontend/` 下对应平台的 `build-ui` 和 `start-ui` 脚本。页面显示的 season／rule／as-of 和发行包内 `manifest.json` 可用于确认当前数据与运行包身份。

## 安装

四个技能会一起安装。安装后不需要手工注册命令，AI 助手会根据每个 `SKILL.md` 的描述自动选择。

### 方法 A：让 AI 助手安装（推荐）

把下面这段交给有 shell 权限的 AI 助手：

> 全局安装 https://github.com/pmwl0128/quark_drive_skills 中的四个技能。同时安装依赖 `quickjs-ng>=0.15`；如果当前环境无法安装，则确认 Node.js 回退可用。最后确认四个 `SKILL.md` 都能被发现，并实际运行一次计算器的 `schema` 命令。

这是默认推荐方式：AI 助手可以识别当前宿主、选择正确目录、处理运行依赖，并在复制后直接验证，而不需要用户手工判断路径或链接方式。

### 方法 B：Claude Code 插件市场

在 Claude Code 中输入。若已安装旧插件，先运行 `/plugin uninstall pokemon-champions@pmwl`：

```text
/plugin marketplace add pmwl0128/quark_drive_skills
/plugin install quark-drive-skills@quark-drive
```

### 方法 C：一行命令

先安装 [Node.js](https://nodejs.org/)，然后在项目目录运行：

```bash
npx skills add pmwl0128/quark_drive_skills
```

全局安装到所有项目：

```bash
npx skills add pmwl0128/quark_drive_skills -g
```

安装器来自 [vercel-labs/skills](https://github.com/vercel-labs/skills)，会提示你选择 AI 助手和安装范围。当前版本在 Windows 上使用链接方式可能失败；遇到此问题时，请改用复制方式或手动创建链接。

### 方法 D：手动复制

```bash
git clone https://github.com/pmwl0128/quark_drive_skills.git
mkdir -p ~/.claude/skills ~/.agents/skills
cp -r quark_drive_skills/.claude/skills/* ~/.claude/skills/   # Claude Code
cp -r quark_drive_skills/.agents/skills/* ~/.agents/skills/   # Codex
```

仓库中的 `.agents/skills/` 与 `.claude/skills/` 是逐字节一致的镜像，只需使用你的 AI 助手会扫描的那一份。

### 环境要求

- **Python 3.10+**：四个技能的 Python 查询入口；dex、meta 和 team 的日常查询只使用标准库。
- **quickjs-ng 0.15+（推荐）**：NCP 伤害/速度计算器的默认进程内运行时，安装后不需要 Node.js；计算器技能自带 `requirements.txt`。
- **Node.js（回退 / 可选）**：未安装 quickjs-ng 时，计算器的 Python 入口会自动回退到 Node；使用方法 C 的 `npx` 安装器时则必须安装。
- **openpyxl 3.1+（可选）**：只有自行运行 meta 的 `export-excel` 时需要；发行版已经附带工作簿。

单独安装计算器运行依赖也可以直接运行：

```bash
python -m pip install "quickjs-ng>=0.15"
```

### 更新

| 安装方式 | 更新方法 |
|---|---|
| 让 AI 助手安装 | 让 AI 助手从同一仓库重新安装四个技能，并复查 quickjs-ng / Node 回退 |
| `npx skills` | `npx skills update` |
| Claude Code 插件 | `/plugin marketplace update` |
| Git / 手动复制 | `git pull` 后重新复制对应 skills 目录 |

### 验证安装

确认 skills 目录中存在以下四个文件夹：

```text
ncp-damage-calculator
pokemon-champions-dex
pokemon-champions-meta
pokemon-champions-team
```

然后先问一句“烈咬陆鲨能学龙之舞吗？”，再问“帮我建一支单打队”。前者应触发 dex，后者应进入 team 的分批引导。

## 新手入门：建立自己的对战项目

技能可以全局安装，但更推荐为自己的队伍建立一个独立项目目录：队伍、持有列表、复盘记录和 AI 助手指令都放在一起，后续改队时更容易持续积累上下文。

### 1. 创建项目目录

```bash
mkdir my-champions
cd my-champions
```

目录名可以任意。使用 IDE 类 AI 助手时直接打开这个文件夹；使用 CLI AI 助手时先进入目录，再启动 Claude Code 或 Codex。

### 2. 安装四个技能

启动对应的 AI 助手后，直接发送下面这段话：

> 从 https://github.com/pmwl0128/quark_drive_skills 将四个技能安装到当前项目，并把仓库中的 `CLAUDE.md` 和 `AGENTS.md` 放到项目目录。同时安装 `quickjs-ng>=0.15`；如果当前环境无法安装，请确认 Node.js 回退可用。最后确认四个 `SKILL.md` 均可识别，并实际运行一次计算器的 `schema` 命令。

也可以使用上面的其他 [安装方式](#安装)。

### 3. 放入自己的数据（可选）

你可以在项目里保存任意格式、任意文件名的个人资料，例如：

```text
my-champions/
  CLAUDE.md           # 可选
  AGENTS.md           # 可选
  roster.md           # 拥有的宝可梦
  current-team.txt    # Showdown 队伍或自然语言配置
  matchup-notes.md    # 实战记录、常见对手与改队想法
```

这些文件都不是固定格式。提问时告诉 AI 助手文件路径和使用方式即可，例如“只用 `roster.md` 里的宝可梦”“结合 `matchup-notes.md` 重新检查这支队”。AI 助手会先读取并清洗内容，再交给相应技能。

### 4. 开始提问并持续迭代

在项目目录里启动 AI 助手后，可以从下面任一句开始：

```text
烈咬陆鲨能学龙之舞吗？
分析 current-team.txt 的合法性和环境对位。
只用 roster.md 里的宝可梦，帮我建一支单打队。
根据 matchup-notes.md 里的实战问题继续改队，不要动我指定的核心。
```

保留每轮队伍与复盘记录，会比每次从空白对话重新生成更容易得到贴近个人打法的结果。

## 模型调用与流程提示

技能的自动触发依赖 AI 助手能正确理解 `SKILL.md` 并持续遵循其中的指令，它不是对所有模型都同样强制的运行时机制。部分工具调用能力或长任务指令遵循能力较弱的模型，可能会直接凭记忆回答、只调用其中一个事实技能，或在建队时跳过部分内置流程。

遇到这类情况，可以在请求中显式指定：

```text
先读取 pokemon-champions-team/SKILL.md，再使用 team 技能完整处理这次建队请求。
这个结论不要凭记忆：名称和合法性查 dex，环境分布查 meta，伤害与速度调用 calculator。
请遵守 team 技能的内置建队流程，不要跳过候选比较、合法性检查和最终核验。
```

同时建议使用发行版附带的 `CLAUDE.md` / `AGENTS.md` 作为项目级指令。它们不能让模型自动获得更高的对战水平，但可以提高技能路由和流程执行的稳定性。若模型仍频繁忽略工具结果，换用工具调用与长指令遵循能力更强的模型通常比反复补救提示更有效。

## 使用建议

1. 在任意项目文件夹中启动 Claude Code、Codex 或其他兼容 Agent Skills 的 AI 助手。
2. 直接用中文、英文或日文提问，不需要记 CLI 命令。
3. 队伍、持有列表和限制条件可以粘贴在对话里，也可以放在任意本地文件中并告诉 AI 助手路径；没有规定文件名。
4. 需要当前环境结论时留意页首 season/rule/as-of；需要更新时更新整个技能包。

## 环境工作簿

每次正式发行会从完整的单打、双打快照与更新报告重新生成三份工作簿，并以固定文件名发布到 [data-latest Release](https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest)（滚动覆盖，只保留最新一期；git 树不携带二进制文件，克隆保持轻量）：

- [workbook_zh.xlsx](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_zh.xlsx)（中文）
- [workbook_ja.xlsx](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_ja.xlsx)（日文）
- [workbook_en.xlsx](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_en.xlsx)（英文）

每份文件只使用一种主语言；中、日版附英文规范名，便于交叉检索。主要工作表包括：

| 工作表 | 内容 |
|---|---|
| 单打 / 双打 | 排名、名称、热门招式、道具、特性、性格、队友和 SP 分布 |
| 更新报告 | 相对上一快照的排名、新进/跌出与配置变化事实，以及名称联动检索 |

工作簿是 facts-only 报告，不包含 AI 对环境的主观解读。

排名趋势折线图 `trend_{single,double}_{zh,ja,en}.png`（单打 / 双打 × 三种语言）发布在同一 Release。它们滚动展示最近至多 10 期（每期对应一次数据刷新）的排名走势：任意一期进入过 Top30 的宝可梦全程追踪，另收录进过前 60 名的大幅变动；纵轴 1–30 名等距、30 名以后压缩，上升 / 下降 / 稳定折线以颜色区分，线两端直接标注宝可梦名。

![单打排名趋势](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/trend_single_zh.png)

![双打排名趋势](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/trend_double_zh.png)

## 数据时效与诚实边界

- meta 与 real-team 数据随环境变化；发行版只保证页首标注日期对应的本地快照，不声称实时。
- dex、meta、team 样本和计算器全部随包查询，日常使用不需要网络；获取新数据请更新技能包。
- 历史 season 可以查询，但旧规则不会静默混入当前规则证据池。
- 图鉴事实和伤害公式也可能随规则更新；跨规则问题必须明确 season/rule。
- 工具无法覆盖所有回合状态、隐藏信息和玩家选择。输出中的假设、置信度与注意事项也是结论的一部分。

## 数据来源与致谢

发行版发布经过转换、校验和脱敏的查询数据与事实投影，不发布开发侧原始抓取缓存。主要来源包括：

### 环境排名与使用率

| 来源 | 用途 |
|---|---|
| [Silph Scope](https://silph-scope.com/) | 主数据源：环境排名，招式、道具、特性、性格、SP、队友详情，KO 关系列表（谁能击倒谁），以及单双打的击倒／被击倒招式占比 |
| [GameWith](https://gamewith.jp/) Pokémon Champions | 备用环境 feed 与交叉验证 |
| [PokeChamp DB](https://pokechamdb.com/) | 备用环境 feed 与交叉验证 |
| [MetaRoll](https://metaroll.app/ladder) | 双打击倒／被击倒招式占比的备用源。Usage data from MetaRoll (metaroll.app) |

### 对战图鉴与名称

| 来源 | 用途 |
|---|---|
| [Serebii.net](https://www.serebii.net/) Champions Pokédex | Champions 学习集、特性与招式先制度 |
| [52Poke / 神奇宝贝百科](https://wiki.52poke.com/) | 中日英显示名、别名与分形态资料 |
| [PokéAPI](https://pokeapi.co/) | 名称独立校验 |

### 对战计算

| 来源 | 用途 |
|---|---|
| [NCP VGC Damage Calculator](https://github.com/nerd-of-now/NCP-VGC-Damage-Calculator) | 核心实体数据与伤害计算引擎 |
| [smogon/damage-calc](https://github.com/smogon/damage-calc) | 独立公式 oracle。开发期测试用它的 `@smogon/calc` 引擎（经独立维护的 [EXO Champions 计算器](https://github.com/SebNotFound/champions-calc) 接入）复算同一批用例，与发行版引擎的伤害结果交叉比对；其代码与数据均不随本发行版分发。 |

### 真实队样本

| 来源 | 形式 | 用途 |
|---|---|---|
| [Yakkun / ポケモン徹底攻略](https://yakkun.com/) | 单打 | 完整联合配置与 SP |
| [OP.GG](https://op.gg/) Pokémon Champions | 单打 | 复刻队伍与 SP |
| [GameWith](https://gamewith.jp/) / [Game8](https://game8.jp/) | 单打 | 已结束赛季的上位构筑事实 |
| [POCH](https://poch.ms/en) | 单打 | 公开队伍索引与经审阅的游戏内导出图片 |
| [Limitless TCG](https://play.limitlesstcg.com/) | 双打 | 线上赛事队伍、战绩与名次 |
| [Nimbasa City Post](https://www.nimbasacitypost.com/) | 双打 | 官方锦标赛公开队伍表（OTL）的镜像 |
| [VGCPastes / Pokepaste](https://pokepast.es/) | 双打 | 社区整理的完整队伍与 SP |

感谢这些项目、站点、赛事组织者和社区贡献者公开数据与工具。来源中的排名、战绩和队伍只作为事实证据使用，不代表本项目对其强度作背书。

特别感谢 [PokeChamp DB](https://pokechamdb.com/) 在技能开发期间提供的协助。

## 后续方向

- **流程效率与兼容性：**减少重复工具调用，实测各类 AI 助手能否完整执行建队流程。
- **回合模拟研究：**在独立实验管线中研究多回合行为。已发布的技能和 Web 仍按各自契约进行伤害、速度与对位计算。
- **在线服务容量：**改善资源交付与响应延迟，依据实际工作量调整服务额度。

## 许可

`ncp-damage-calculator` 内置 NCP VGC Damage Calculator 公式；上游许可保留在技能的 `references/upstream-LICENSE`，并在 `NOTICE` 中说明。本发行版其余许可见 `LICENSE`（MIT）。

> Pokémon 及角色名称为 Nintendo / Creatures Inc. / GAME FREAK inc. 的商标。本项目是非官方同人工具，与上述公司无关，也未获其背书。
