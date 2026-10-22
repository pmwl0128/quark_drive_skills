<h1 align="center">Quark Drive Skills · 夸克充能</h1>

<p align="center">
  <img alt="skills" src="https://img.shields.io/badge/skills-4-blue"/>
  <img alt="modes" src="https://img.shields.io/badge/single%20%26%20double-supported-success"/>
  <img alt="query" src="https://img.shields.io/badge/query-offline--first-success"/>
  <img alt="languages" src="https://img.shields.io/badge/names-zh%20%7C%20en%20%7C%20ja-orange"/>
  <img alt="agents" src="https://img.shields.io/badge/Claude%20Code%20%7C%20Codex-Agent%20Skills-blueviolet"/>
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green"/>
</p>

<p align="center">
  <b>环境快照</b> · M-7 / M-C · 更新于 <b>2026-10-21</b>
</p>

<p align="center">
  <a href="README.md">English</a> | <b>中文</b> | <a href="README_ja.md">日本語</a>
</p>

<p align="center">
  <a href="https://champion.mpan.top">打开 Quark Drive Web</a> · <a href="https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest">下载与数据表</a>
</p>

---

用于 AI Agent 的《宝可梦冠军》对战工具 Skills：支持图鉴信息、环境查询、伤害计算等事实查询，以及基于事实和工作流的建队、诊断和 SP 调整等任务。支持单打、双打，以及中英日名称检索，可供 Claude Code、Codex、WorkBuddy 等能加载 Agent Skills、运行本地 Python 的助手使用。

## 安装

需要 **Python 3.10 或更高版本**。伤害计算器推荐安装 `quickjs-ng`；未安装时会尝试使用 Node.js。普通图鉴、环境和队伍查询不需要额外的 Python 库。

如果助手能执行终端命令，直接把这段话发给它即可：

> 从 https://github.com/pmwl0128/quark_drive_skills 安装全部四个技能，放到当前助手能识别的位置。安装 `quickjs-ng>=0.15`，如果装不上，就确认 Node.js 备用方式可用。检查四份 `SKILL.md` 都能被发现，再运行一次计算器的 `schema` 命令。

也可以按所用工具自行安装：

| 工具 | 安装方式 |
|---|---|
| Claude Code | 使用下方插件命令，或把仓库 `.agents/skills/` 下的四个技能目录复制到 `~/.claude/skills/`。 |
| Codex | 把四个技能目录复制到 `~/.agents/skills/`。 |
| WorkBuddy | 在技能页选择「添加技能 → 上传技能」，导入本地技能包，操作见 [WorkBuddy 官方说明](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)。 |
| 其他兼容助手 | 复制到该工具的技能目录，或使用下方的 `skills` 安装器。 |

在 Claude Code 中执行：

```text
/plugin marketplace add pmwl0128/quark_drive_skills
/plugin install quark-drive-skills@quark-drive
```

若装过旧插件，先执行 `/plugin uninstall pokemon-champions@pmwl`。

装有 Node.js 时，也可用 [Vercel Skills CLI](https://github.com/vercel-labs/skills) 选择目标助手并安装：

```bash
npx skills add pmwl0128/quark_drive_skills
```

加 `-g` 表示全局安装。手动安装则先下载或克隆仓库，复制四个技能目录，再安装计算器依赖：

```bash
python -m pip install "quickjs-ng>=0.15"
```

完成后应能找到 `pokemon-champions-dex`、`pokemon-champions-meta`、`ncp-damage-calculator`、`pokemon-champions-team` 四个目录。新开一次会话，先问一个图鉴问题，再提一个建队需求，检查助手是否调用了相应技能。

更新时，按原安装方式执行 `npx skills update`、`/plugin marketplace update`，或重新复制、导入最新版本，四个技能一起更新。仓库的 `.claude/skills/` 与 `.agents/skills/` 内容相同。

## 使用方法

Web 和 Console 的队伍输入框、导入菜单，以及 AI 问答和队伍诊断支持队伍码，每次导入一个公开队伍码。运行者需要配置自有查询身份或自己的查询网关，配置与可选联网依赖见[队伍码指南](.agents/skills/pokemon-champions-team/references/team-codes.md)。

自然语言直接描述需求。队伍可以粘贴 Showdown 文本、提供清晰截图，也可以放在本地文件里，让助手读取；

| 技能 | 适合处理的问题 |
|---|---|
| `pokemon-champions-dex` | 属性、种族值、特性、学习集、招式、道具、性格、超级石，以及中英日名称对应和条件反查。 |
| `pokemon-champions-meta` | 单双打使用率、常见配置与队友、SP 分布、历史环境及本次更新的变化。 |
| `ncp-damage-calculator` | 伤害乱数、确一／确二与击倒概率、耐久线、速度线，以及顺风、戏法空间等条件下的比较。 |
| `pokemon-champions-team` | 组队、补位、改队、合法性与结构检查、环境对位、单打六选三／双打六选四和 SP 调整；按需调用前三个技能。 |

比如：

```text
围绕超级巨金怪组一个双打顺风队。
我的队伍在 team.txt ，对位当前环境前 30 有哪些困难点并改进，要求保留炽焰咆哮虎，说明换人的收益和代价。
只用 roster.md 里我已有的宝可梦组队，并说明遇到这类对手时怎么选出。
```

长期使用时，建议建立项目目录，存放持有列表、各版队伍和对战笔记，并将仓库里的 [AGENTS.md](AGENTS.md) 或 [CLAUDE.md](CLAUDE.md) 作为助手的项目说明。实战后输入你的经验和要求作为反馈 ，能够帮助 AI 助手更好的理解你的意图并完成工作。

### 结果该怎么看

工具负责查数据、计算和规则检查，助手负责选方案、讲取舍。建队时会先确认限制，参考环境和真实队伍，比较候选，再核对最终结果。真实数据和工作流约束能减少记错数据、配出非法招式或算错伤害等问题，复杂需求如建队/诊断结果是否合理，仍需要实战检验。选用基础能力更强、世界知识更丰富的模型能显著提高结果质量。

如果助手偶尔跳过工具、仍自由发挥作答，可以明确要求：「先读 `pokemon-champions-team/SKILL.md`，按完整流程完成，不要跳过候选比较与最终核对。」不同助手和模型对技能的调用、对长流程的执行并不一致。

- **数据仅适用于《宝可梦冠军》。** 种族值、学习集和机制可能与宝可梦正作不同，未在宝可梦冠军中开放的内容不在支持范围内。
- **合法性有三种结果。** `valid` 表示通过已有检查，`invalid` 有明确错误，`unknown` 表示信息不足。
- **对位结论有条件。** 要看清具体配置、能力等级、场地和假设。KO／CHECK 表主要依据伤害、速度及有限的回合条件，不能涵盖所有强化、回复、异常状态和长期周旋。分析自己的队伍时应使用实际配置；标准配置只是置信度较低的参考。

## 网页与本地工作台

访问 **[Quark Drive Web](https://champion.mpan.top)** 可先行体验基本功能 ，包括浏览环境、图鉴，计算伤害与速度，查看对位表和保存队伍。浏览及浏览器内计算不需要账号。在线问答、简化建队、诊断和解读按当前开放功能与额度提供；建队向导提交一次表单、返回一次结果，持续讨论和改队可交给本地助手。

本地界面名为 **Quark Drive Console**，提供工作区和会话页面：把任务交给助手后，可以在页面上回答问题、确认方案、查看产出。

| 功能 | 在线版 | 本地版 |
|---|---|---|
| 环境、图鉴、伤害／速度、对位表和快速耐久试算 | 支持 | 支持 |
| 队伍、宝可梦箱、计算记录 | 存在当前浏览器 | 存在本地工作区 |
| 精确 SP 调整 | 依当前开放功能与服务额度 | 支持 |
| AI 建队与改队 | 使用在线服务与额度 | 使用你配置的本地助手 |
| 会话交接、提问与方案确认 | 不支持 | 支持 |

### 启动本地版

下载 [Quark_Drive_Web.zip](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/Quark_Drive_Web.zip)，解压后在该文件夹打开终端。预构建包需要 Python 3.10+，不需要 Node.js。

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

打开启动时打印的完整 `http://127.0.0.1:<port>/#bootstrap=...` 链接。链接只能用一次，换浏览器或需要重新登录时，在解压目录执行 `python bridge/pcui open` 获取新链接。端口可在 `~/.pokemon-champions/pc.env` 中通过 `PCUI_LOCAL_PORT` 修改。

连接助手时按本地首页的引导卡操作。`python bridge/pcui install-agent` 安装配套指令；Codex 加 `--target codex`。把会话页的交接指令发给助手，之后可在页面回答问题和确认方案。支持 MCP 的客户端也可连接 `python bridge/pcui mcp`，加 `--print-config` 可打印 Claude Code、Codex 的注册配置。

<details>
<summary>从源码构建</summary>

需要 Python 3.10+，以及与发布校验一致的 Node.js 22。

```powershell
git clone https://github.com/pmwl0128/quark_drive_skills.git
Set-Location .\quark_drive_skills
python -m pip install -r frontend/requirements-ui.txt
.\frontend\build-ui.ps1
.\frontend\start-ui.ps1
```

Linux/macOS 将对应的 `frontend/build-ui.sh`、`frontend/start-ui.sh` 设为可执行后运行。构建脚本会安装 npm 依赖、生成数据投影并构建界面；bridge、技能、投影和界面应来自同一版本。

</details>

### 保存、备份和更新

本地服务默认只监听 `127.0.0.1`，浏览和计算不需要模型 API Key。预构建包的工作区默认位于 `~/.pokemon-champions/workspace`，源码版默认位于启动目录的 `storage`，可在「存储」页迁移。备份时先停止服务，再复制整个工作区文件夹。

在线 AI 功能会把相关输入发送到服务端。本地工具虽然在你的电脑上运行，助手本身是否发送内容、发往哪里，仍由其模型服务和配置决定。

更新本地版时，把完整新包解压到新目录。若曾把工作区放进旧程序目录，先迁出再删除旧目录。源码版更新后重新运行构建、启动脚本；不要只替换部分生成目录，以免混用不同版本。

## 环境数据与下载

技能日常查询读取随包附带的本地数据。环境会变化，使用前请核对页首或界面里的赛季、规则和快照日期，需要新数据时更新整套技能。历史赛季数据可以通过excel查询，当前最新发行版本的 Skills 不会携带历史规则和数据。

[data-latest](https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest) 滚动提供：

- [中文](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_zh.xlsx)、[英文](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_en.xlsx)、[日文](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_ja.xlsx) 环境工作簿，含单双打完整表和本次变化记录，只列事实，不加 AI 解读。自行执行 `export-excel` 时需安装 `openpyxl>=3.1`。
- [单打](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/trend_single_zh.png)、[双打](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/trend_double_zh.png) 排名趋势图，最多显示最近十次更新，另有英日版本。KO 目标与威胁列表显示名次，不是使用率百分比；采集时间和覆盖范围也可能不同。
- 上述本地运行包。滚动文件随发布替换。

### 来源与致谢

| 内容 | 来源 |
|---|---|
| 环境数据 | 主源 [Silph Scope](https://silph-scope.com/)；[GameWith](https://gamewith.jp/)、[PokeChamp DB](https://pokechamdb.com/)、[MetaRoll](https://metaroll.app/ladder) 作备用。 |
| 图鉴与名称 | [Serebii](https://www.serebii.net/)、[神奇宝贝百科](https://wiki.52poke.com/)、[PokéAPI](https://pokeapi.co/) |
| 伤害计算 | 内置 [NCP VGC Damage Calculator](https://github.com/nerd-of-now/NCP-VGC-Damage-Calculator) 核心；[smogon/damage-calc](https://github.com/smogon/damage-calc) 独立核验 |
| 单打队伍 | [ポケモン徹底攻略](https://yakkun.com/)、[OP.GG](https://op.gg/)、[GameWith](https://gamewith.jp/)、[Game8](https://game8.jp/)、[POCH](https://poch.ms/en) |
| 双打队伍 | [Limitless](https://play.limitlesstcg.com/)、[Nimbasa City Post](https://www.nimbasacitypost.com/)、[VGCPastes / Pokepaste](https://pokepast.es/) |

感谢公开数据和工具的作者、赛事组织者与社区贡献者。发布包只包含整理后的查询数据，不分发原始抓取缓存。

特别感谢 [Silph Scope](https://silph-scope.com/) 在环境数据查询接口和队伍码查询方面提供的帮助。

## 后续方向

- 减少重复调用，改善不同助手完成建队流程的稳定性。
- 继续改进队伍分析，让建议更能考虑对局中的选择和变化。
- 改善在线加载和响应速度，按实际使用情况调整服务容量。

## 许可

项目采用 [MIT 许可](LICENSE)。NCP 计算器的上游许可保留在 `ncp-damage-calculator/references/upstream-LICENSE`，署名说明见 [NOTICE](NOTICE)。

Pokémon 及相关角色名称是 Nintendo / Creatures Inc. / GAME FREAK inc. 的商标。本项目是非官方同人工具，与上述公司无关联，也未获其背书。
