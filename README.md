<h1 align="center">Quark Drive Skills</h1>

<p align="center">
  <img alt="skills" src="https://img.shields.io/badge/skills-4-blue"/>
  <img alt="modes" src="https://img.shields.io/badge/single%20%26%20double-supported-success"/>
  <img alt="query" src="https://img.shields.io/badge/query-offline--first-success"/>
  <img alt="languages" src="https://img.shields.io/badge/names-zh%20%7C%20en%20%7C%20ja-orange"/>
  <img alt="agents" src="https://img.shields.io/badge/Claude%20Code%20%7C%20Codex-Agent%20Skills-blueviolet"/>
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green"/>
</p>

<p align="center">
  <b>Metagame snapshot</b> · M-7 / M-C · updated <b>2026-10-21</b>
</p>

<p align="center">
  <b>English</b> | <a href="README_zh.md">中文</a> | <a href="README_ja.md">日本語</a>
</p>

<p align="center">
  <a href="https://champion.mpan.top">Try Quark Drive Web</a> · <a href="https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest">Downloads</a>
</p>

---

Four Agent Skills for Pokémon Champions: dex and metagame lookups, damage calculations, and team building, diagnosis, and SP tuning grounded in data and a defined workflow. Support Singles, Doubles, and names in English, Chinese, and Japanese. Use them with Claude Code, Codex, WorkBuddy, or another agent that can load Agent Skills and run local Python.

## Install

Requires Python 3.10 or later. The calculator uses `quickjs-ng` by default and falls back to Node.js if it is unavailable. Ordinary dex, metagame, and team queries need no other Python packages.

Team-code imports also work in team text inputs and import menus across Web and Console, and in assistant questions and team diagnosis. Enter one public code per import. Operators must configure an independently registered query identity or their own query gateway; setup and optional network dependencies are documented in the [team-code guide](.agents/skills/pokemon-champions-team/references/team-codes.md).

The easiest option is to give an assistant with terminal access this request:

> Install all four skills from https://github.com/pmwl0128/quark_drive_skills for this agent. Install `quickjs-ng>=0.15`, or verify the Node.js fallback if that fails. Check that the agent can discover every `SKILL.md`, then run the calculator's `schema` command.

You can also install them yourself:

| Agent | Installation |
|---|---|
| Claude Code | Use the plugin commands below, or copy the four folders from `.agents/skills/` into `~/.claude/skills/`. |
| Codex | Copy the four folders from `.agents/skills/` into `~/.agents/skills/`. |
| WorkBuddy | Import local skill packages through **Add skill → Upload skill** in the Skills page. See the [official instructions](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market). |
| Other compatible agents | Copy the four skill folders into the directory your agent reads, or use the `skills` installer below. |

Claude Code plugin commands:

```text
/plugin marketplace add pmwl0128/quark_drive_skills
/plugin install quark-drive-skills@quark-drive
```

If you still have the old plugin, remove it first with `/plugin uninstall pokemon-champions@pmwl`.

With Node.js installed, the [Vercel Skills CLI](https://github.com/vercel-labs/skills) offers an interactive installer:

```bash
npx skills add pmwl0128/quark_drive_skills
```

Add `-g` to install globally. For a manual installation, download or clone this repository, copy the four skill directories, then install the calculator runtime:

```bash
python -m pip install "quickjs-ng>=0.15"
```

The installed folders should be `pokemon-champions-dex`, `pokemon-champions-meta`, `ncp-damage-calculator`, and `pokemon-champions-team`. Start a new agent session and ask a dex question, then a team-building question, to check that it selects the right skill.

To update, use `npx skills update`, `/plugin marketplace update`, or repeat your copy/import installation with the latest repository contents. Update all four skills together. The `.claude/skills/` tree is a mirror of `.agents/skills/`.

## How to use

Describe what you want in your own words. Paste a Showdown export, attach a readable screenshot, or ask the assistant to read a local team or roster file.

| Skill | What to ask |
|---|---|
| `pokemon-champions-dex` | Types, base stats, abilities, learnsets, items, natures, and Mega Stones. Supports name lookup across all three languages and questions such as “Which Fairy-types learn Earth Power?” |
| `pokemon-champions-meta` | Current and historical usage, common moves and items, partners, SP spreads, and changes between snapshots. Singles and Doubles are kept separate. |
| `ncp-damage-calculator` | Damage rolls, OHKO/2HKO chances, survival benchmarks, and Speed tiers under specified sets and field conditions. |
| `pokemon-champions-team` | Build or revise a team, check legality and coverage, compare matchups, plan bring-three/bring-four selections, and tune SP to specific benchmarks. It calls the other skills as needed. |

For example:

```text
Build a Tailwind Doubles team around Mega Metagross.
Improve the team in team.txt against the current top 30. Keep Incineroar, and explain the benefits and trade-offs of each replacement.
Use only the Pokémon in roster.md, and suggest a bring-four plan for this matchup.
```

For ongoing use, keep your roster, team versions, and battle notes in a project folder, with this repository's [AGENTS.md](AGENTS.md) or [CLAUDE.md](CLAUDE.md) as the agent instructions. Feed back your experience and requirements after playing. That gives the assistant a clearer idea of what you want from the next revision.

### What the results mean

The tools handle data, arithmetic, and legality checks; your assistant chooses candidates and explains the trade-offs. Team work starts by checking your constraints, consulting the metagame and observed teams, and comparing candidates before checking the final recommendation. Data and workflow requirements help prevent incorrect facts, illegal movesets, and calculation errors. More involved work, such as building or diagnosing a team, still needs to be judged in battle. A more capable model with broader background knowledge can substantially improve the result.

If the assistant occasionally skips tools and improvises an answer, ask it to read `pokemon-champions-team/SKILL.md` and follow the full workflow, including candidate comparison and final checks. Skill activation and instruction following vary by agent and model.

- **Champions data only.** Stats, learnsets, and mechanics can differ from the main-series games. Only content available in Champions is covered.
- **Legality has three outcomes.** `valid` passed the available checks; `invalid` has identified errors; `unknown` needs more information.
- **Calculations depend on the input.** State the sets, stat stages, field, and assumptions. KO/CHECK tables describe the evaluated damage and Speed relationships, with limited coverage of setup, recovery, status, and longer sequences. Use your actual sets when reviewing your own team; standard sets are references with lower confidence.

## Browser interface

Try the basic tools in **[Quark Drive Web](https://champion.mpan.top)**: browse the metagame and dex, calculate damage and Speed, check matchup tables, and save teams. Browsing and browser calculations need no account. Online Q&A, the simplified builder, diagnosis, and explanations depend on enabled features and service quotas. The builder returns one result from a form; ongoing team discussion is available through a local assistant.

The downloadable local interface is **Quark Drive Console**. It adds a local workspace and sessions where you can pass work to an assistant, answer its questions, and review its results.

| | Online | Local |
|---|---|---|
| Rankings, dex, calculations, matchup tables, quick survival checks | Available | Available |
| Teams, Pokémon Box, calculation history | Stored in this browser | Stored in a workspace folder |
| Exact SP tuning | Subject to enabled features and service limits | Available |
| AI team work | Online features and quotas | Your local assistant and its own setup |
| Session handoffs and checkpoint decisions | Unavailable | Available |

### Run the local bundle

Download [Quark_Drive_Web.zip](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/Quark_Drive_Web.zip), extract it, and open a terminal in the extracted folder. You need Python 3.10+, but no Node.js for this prebuilt bundle.

Windows PowerShell:

```powershell
python -m pip install -r requirements-runtime.txt
.\start-local.ps1
```

Linux / macOS:

```bash
python3 -m pip install -r requirements-runtime.txt
chmod +x start-local.sh
./start-local.sh
```

Open the full `http://127.0.0.1:<port>/#bootstrap=...` link printed by the launcher. Each link works once. Run `python bridge/pcui open` from the extracted folder for another link. To change the port, set `PCUI_LOCAL_PORT` in `~/.pokemon-champions/pc.env`.

For assistant collaboration, follow the local home page's setup card. `python bridge/pcui install-agent` installs the companion instructions; add `--target codex` for Codex. Send the handoff from a session page to your assistant, then answer questions and checkpoint decisions on that page. MCP clients can use `python bridge/pcui mcp`; `--print-config` prints registrations for Claude Code and Codex.

<details>
<summary>Build from source</summary>

You need Python 3.10+ and Node.js 22, matching release verification.

```powershell
git clone https://github.com/pmwl0128/quark_drive_skills.git
Set-Location .\quark_drive_skills
python -m pip install -r frontend/requirements-ui.txt
.\frontend\build-ui.ps1
.\frontend\start-ui.ps1
```

On Linux/macOS use `frontend/build-ui.sh` and `frontend/start-ui.sh` after making them executable. The build script installs npm dependencies, builds the data projection, and builds the UI. Keep the bridge, skills, projection, and UI from the same snapshot.

</details>

### Storage, privacy, and updates

Local browsing and calculations require no hosted-model key, and the service listens on `127.0.0.1` by default. The prebuilt bundle stores your workspace in `~/.pokemon-champions/workspace`; a source checkout uses `storage` under the launch directory. You can move it on the Storage page. Stop the service and copy the entire workspace folder to back it up.

Online AI features send the relevant input to the service. A local assistant follows its own provider's data handling, even when the tools run locally.

To update the bundle, extract a complete new download into a new folder. If you put the workspace inside the old application folder, move it before deleting that folder. For source installations, update the checkout and rerun the build/start scripts. Replacing individual generated folders can mix incompatible snapshots.

## Data and downloads

Normal skill queries read the bundled data locally. Check the season, regulation, and snapshot date above or in the UI, and update the package when you need newer data. Historical seasons can be consulted through the Excel workbooks; the latest Skills release does not include historical regulations or datasets.

The rolling [data-latest release](https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest) includes:

- Workbooks in [English](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_en.xlsx), [Chinese](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_zh.xlsx), and [Japanese](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_ja.xlsx), with Singles/Doubles tables and a factual update report. No AI commentary is added. Running `export-excel` yourself requires `openpyxl>=3.1`.
- [Singles](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/trend_single_en.png) and [Doubles](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/trend_double_en.png) rank charts covering up to ten recent refreshes, also available in Chinese and Japanese. KO lists show ranks, not usage percentages; their coverage and capture dates can differ from usage data.
- The prebuilt local interface linked above. Rolling files are replaced with each release.

### Sources and thanks

| Data | Sources |
|---|---|
| Metagame | [Silph Scope](https://silph-scope.com/) is the primary source; [GameWith](https://gamewith.jp/), [PokeChamp DB](https://pokechamdb.com/), and [MetaRoll](https://metaroll.app/ladder) provide fallback data. |
| Dex and names | [Serebii](https://www.serebii.net/), [52Poke](https://wiki.52poke.com/), [PokéAPI](https://pokeapi.co/) |
| Calculation engine | Bundled [NCP VGC Damage Calculator](https://github.com/nerd-of-now/NCP-VGC-Damage-Calculator) core, independently checked against [smogon/damage-calc](https://github.com/smogon/damage-calc). |
| Singles teams | [Yakkun](https://yakkun.com/), [OP.GG](https://op.gg/), [GameWith](https://gamewith.jp/), [Game8](https://game8.jp/), [POCH](https://poch.ms/en) |
| Doubles teams | [Limitless](https://play.limitlesstcg.com/), [Nimbasa City Post](https://www.nimbasacitypost.com/), [VGCPastes / Pokepaste](https://pokepast.es/) |

Thanks to the authors, tournament organizers, and contributors who publish these resources. The release contains processed query data, not raw scrape caches.

Special thanks to [Silph Scope](https://silph-scope.com/) for their help with the metagame query API and team-code lookup.

## Roadmap

- Reduce repeated tool calls and improve workflow consistency across agents.
- Improve team analysis where battle context and uncertain information affect the recommendation.
- Improve online delivery and response times, and adjust capacity to actual demand.

## License

The project uses the [MIT license](LICENSE). The NCP calculator's upstream terms are retained in `ncp-damage-calculator/references/upstream-LICENSE`; see [NOTICE](NOTICE) for attribution.

Pokémon and character names are trademarks of Nintendo / Creatures Inc. / GAME FREAK inc. This is an unofficial fan project with no affiliation or endorsement.
