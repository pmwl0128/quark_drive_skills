<h1 align="center">Quark Drive Skills</h1>

<p align="center">Quark Drive · 夸克充能 · クォークチャージ</p>

<p align="center">
  <b>Help your AI agent use local facts, current metagame data, and exact calculations to look up the dex, calculate matchups, review teams, and build them</b>
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
  <b>Metagame snapshot</b> · M-7 / M-C · updated <b>2026-10-18</b>
</p>

<p align="center">
  <b>English</b> | <a href="README_zh.md">中文</a> | <a href="README_ja.md">日本語</a>
</p>

---

Quark Drive Skills packages four Agent Skills for Pokémon Champions. The optional browser interface is called **Quark Drive Web**; both are delivered from this repository. Together they let an AI agent:

- look up canonical battle-dex facts;
- inspect current and historical metagame distributions;
- calculate damage, KO, survival, and speed lines;
- build, review, revise, select, and tune teams through an audited workflow.

All four skills are installed side by side and routed as peers. `pokemon-champions-team` coordinates the other three when the request concerns an entire team, but it does not replace them: a focused dex, usage, damage, or Speed question goes straight to the relevant factual skill.

Queries read the data shipped with the package. The goal is not to manufacture a hidden “team strength” score, but to make the agent's facts, calculations, assumptions, and trade-offs inspectable.

## Product Positioning

| What it is | What it is not |
|---|---|
| A local skill set for Claude Code, Codex, and compatible agents | A web account or visual interface required to use the skills |
| Structured access to dex, metagame, calculation, and real-team evidence | A chatbot expected to answer from model memory |
| A team-building workflow with legality checks, evidence, confidence, and audit gates | A black-box optimizer that produces an objectively best team |
| An updatable snapshot tied to a season and regulation | A permanently live database that never needs a date stamp |

Scripts own facts, arithmetic, and deterministic checks. The model interprets your goal, designs candidates, weighs trade-offs, and explains the recommendation. That division of responsibility is the core design of the project.

### Quark Drive Web: optional browser interface

All four skills are fully usable through an agent CLI. To make the data, configurations, and generation process easier to inspect, the project also provides an optional companion browser front end. It lets you browse the dex and metagame snapshots visually and assists with matchup, damage, Speed, tuning, and team-diagnosis work:

- **Online:** open [champion.mpan.top](https://champion.mpan.top) with no installation required. Because the online edition has limited resources, some features are simplified compared with the local edition.
- **Local:** download [`Quark_Drive_Web.zip`](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/Quark_Drive_Web.zip) from the [`data-latest` release](https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest). The prebuilt bundle requires Python 3.10+ and its runtime dependencies; it needs no Node.js, account, or model API key for ordinary browsing and calculations.

The interface is not a prerequisite for the skills and does not replace the complete local collaboration workflow with an AI assistant. See [Quark Drive Web](#quark-drive-web) for feature differences, dependencies, and usage instructions.

> [!NOTE]
> **The supported data domain is Pokémon Champions only.**
>
> The four skills support Pokémon, forms, moves, items, abilities, and rules that are officially available in the current Pokémon Champions release. Historical seasons are Champions snapshots as well. The package does not provide main-series datasets for games such as Pokémon Sword and Shield or Pokémon Scarlet and Violet, nor does it cover unreleased content. A familiar Pokémon or move may have different stats, power, abilities, learnsets, or mechanics in Champions; every lookup and calculation follows the current Champions values.

## Skills at a Glance

| Skill | Best suited for |
|---|---|
| [`pokemon-champions-dex`](#pokemon-champions-dex) | “What is it, what can it learn, and which entity does this name mean?” |
| [`pokemon-champions-meta`](#pokemon-champions-meta) | “What is common now, how is it commonly used, and what changed?” |
| [`ncp-damage-calculator`](#ncp-damage-calculator) | “How much does this hit do, does it secure the KO or survive the hit, and which side moves first?” |
| [`pokemon-champions-team`](#pokemon-champions-team) | “Build a team, review this roster, revise it, plan selection, or tune SP.” |

## `pokemon-champions-dex`

An offline battle dex and the canonical name authority for the entire package. It covers Pokémon and forms, typing, base stats, abilities, learnsets, moves, items, natures, and Mega Stone mappings, with batch lookup and multi-condition reverse search.

**What it does well:**

- resolves Chinese, English, and Japanese names, including common aliases and multilingual Mega forms;
- normalizes pasted teams, owned rosters, and set details in batches;
- answers reverse queries such as “who learns this move?” or “which Pokémon satisfy these type, ability, and Speed conditions?”;
- supplies the canonical identifiers shared by meta, calculator, and team.

**Boundary:** a successful dex lookup proves an entity or legal learnset fact. It does not show that a choice is popular or strategically advisable.

Example requests:

```text
Can Garchomp learn Ice Punch in Pokémon Champions?
Which Fairy-types learn Earth Power?
What are Mega Charizard X's base stats and abilities?
Which Pokémon uses this Mega Stone?
```

## `pokemon-champions-meta`

An offline-first metagame snapshot for what players are actually using. Rankings and detail panels are separated by season, regulation, and Singles/Doubles, and include moves, items, abilities, natures, partners, and SP distributions.

**What it does well:**

- summarizes the top of a Singles or Doubles environment;
- shows one Pokémon's common configurations and partners;
- reverse-searches common users of a move, item, or configuration trait;
- compares Singles and Doubles usage;
- reports factual changes from the previous snapshot.

**Boundary:** moves, items, abilities, natures, partners, and spreads in a detail panel are generally independent marginal distributions. Their top entries must not be stitched into an asserted joint set, and usage is not proof of strength.

Example requests:

```text
What are the top 20 Pokémon in the current Doubles snapshot?
How do Mega Metagross movesets differ between Singles and Doubles?
Which Pokémon rose or fell this update, and which item distributions changed?
Which partners commonly appear around Tailwind setters in Doubles?
```

The latest three single-language workbooks (`workbook_{zh,ja,en}.xlsx`, containing the complete Singles and Doubles tables plus a factual update report) are published under fixed names on the [data-latest release](https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest), replaced on every release. They can be read without running any script; check the snapshot date in the sheet header.

## `ncp-damage-calculator`

A Pokémon Champions damage and Speed calculator backed by the bundled NCP VGC Damage Calculator core. It accepts concrete attacker and defender states—including SP, nature, item, ability, stat stages, move, and field—and returns full damage rolls, percentages, KO/survival results, and move order.

**What it does well:**

- calculates a specific attack's range and OHKO/2HKO line;
- checks whether a defined set survives a hit;
- compares two complete Speed states;
- handles Speed lines under Tailwind, Trick Room, Choice Scarf, and weather;
- supplies exact calculations to team matchup and tuning workflows.

**Boundary:** a calculation does not certify Champions legality. The calculator is also not a full turn simulator: recovery loops, status progression, PP, hidden information, and branching player choices remain outside its scope unless explicitly modeled. Any omitted set or field detail is an assumption and should be reported as such.

Example requests:

```text
How much does 32 Attack Adamant Mega Metagross Earthquake do to this Mega Raichu Y?
Does this Incineroar always survive the common Garchomp Earthquake line?
Which side moves first under Tailwind, and how does Trick Room change the order?
How many more Speed SP does this set need to cross the target line?
```

## `pokemon-champions-team`

The dedicated skill for whole-team work: building from scratch, completing a partial roster, validating legality, diagnosing structure, reviewing matchups, planning battle selection, revising members, and tuning SP. It is published alongside the other skills, and coordinates their dex facts, metagame evidence, and exact calculations inside a single auditable task.

It is deliberately not a one-prompt team generator. Its purpose is to help the model produce a transparent recommendation grounded in your intent, the current environment, real joint teams, and reproducible matchup facts—without turning team building into a synthetic score.

> [!IMPORTANT]
> **It raises the floor of an AI team-building answer; it does not replace expert team-building experience.**
>
> The workflow reduces common failures such as stale memory, name mismatches, illegal sets, bad arithmetic, fabricated joint sets assembled from marginal usage, and forgotten constraints. What it improves is factual reliability and reasoning discipline. Candidate quality and explanatory depth still vary with the model driving the skills. Whether a team fits your piloting habits, selection patterns, expected opponents, and in-game plan also depends on the battle context you provide.

Do not treat the first result as an automatically optimal team. Better results come from supplying a win condition, intended leads or selection plans, recurring opponents, acceptable weaknesses, observed losses, and any human insight you already trust. The most productive loop is usually: start with a grounded proposal, test or review it, report what failed, and ask the agent to revise the team or tune specific lines.

### What You Can Provide

There is no required filename or user-facing schema. The agent can work from:

- a request to build around one Pokémon;
- a pasted Showdown-style team;
- an existing team JSON object;
- prose describing members, moves, and SP;
- any local file containing a team or owned roster;
- a clear screenshot that the agent can read first;
- constraints such as “owned Pokémon only,” “keep this Mega,” or “no Trick Room.”

The agent resolves every Pokémon, move, item, ability, and nature through the dex, then turns the team facts, hard constraints, soft preferences, and matchup goals into structured input that the tools can verify. You do not need to clean the format yourself.

### Capability Map

| Capability | What the team skill actually does |
|---|---|
| **Input and legality** | The agent converts prose, screenshots, and files into structured team input. Scripts parse Showdown or JSON and check names, available Pokémon, learnsets, abilities, items, Species/Item Clause, team size, SP limits, and Mega relationships. Results distinguish `valid`, `invalid`, and `unknown`. |
| **Team diagnosis** | Surfaces defensive typing, offensive coverage, Speed structure, speed control, and functional signals without collapsing them into one score. |
| **Matchups and selection** | Computes both directions of damage, Speed, and KO relationships against the current meta top-K; analyzes Singles 6-pick-3, Doubles 6-pick-4, and one-Mega-per-battle constraints without naming an objectively best selection. |
| **SP tuning** | Turns “survive X,” “outspeed Y,” or “OHKO/2HKO Z” into explicit matchup targets and finds the minimum SP needed to cross each discrete threshold, including the opportunity cost. It does not search for one globally optimal spread. |
| **Candidates and real-team evidence** | Retrieves additions and replacements from diagnosed gaps, compares before/after facts, and uses joint structures, representative sets, and complete observed teams as evidence for the model's own assembly. |
| **Result checks** | Applies the same factual checks to every candidate, rejects illegal or hard-constraint-breaking proposals, and checks the final answer for missing assumptions, trade-offs, tuning notes, or unreproducible numbers. |

### How a Team Task Runs

Internally, the skill uses a structured workflow to avoid “generate from intuition, justify afterward.” End users do not need to learn its operators. A complete task can be understood in four stages:

1. **Understand the target:** establish format, anchor, availability, hard constraints, play preference, and required matchup lines. Open-ended builds ask a few focused questions at a time; existing-team analysis asks only for missing information that matters.
2. **Ground the build:** consult both the current metagame and real joint teams, keeping “what is popular” separate from “which elements actually co-occur,” so independent usage columns do not become a fictional set.
3. **Compare candidates:** develop one or more directions, run the same legality, structure, matchup, and confidence checks, and explain what each change fixes and what it gives up.
4. **Validate before delivery:** calculate the relevant damage, Speed, selection, and SP thresholds, then recheck the final prose, numbers, assumptions, and trade-offs before presenting the result for your judgment and iteration.

Following the workflow records evidence, checks constraints, and makes quantitative conclusions reproducible. The agent must still follow the gates and report their results accurately. It cannot determine that one battle philosophy is universally correct or certify a team as objectively optimal.

### Common Ways to Use It

| Your request | What the skill does |
|---|---|
| **Build from scratch** | “Start the team-building flow” enters the full guided intake. A normal build request asks only for missing information, then returns directions that have been compared and validated. |
| **Review or revise a team** | Parses the current roster, checks legality and structure first, adds matchup work when needed, and explains the benefit and cost of each replacement. A user-locked anchor is not silently removed. |
| **Plan selection, review matchups, or tune SP** | Routes the task to 6-pick-3/4 analysis, a full metagame matchup table, or a concrete threshold calculation. “Does this set survive?” and “change the set so it survives” are not treated as the same calculation. |

### How Real-Team Evidence Is Used

The release includes real-team samples partitioned by season, regulation, and format. Their distinctive value is **joint evidence**: which six Pokémon appeared together, whether a member's item/ability/moves/nature/SP came from one observed set, and which structures recur within a given evidence tier.

The library is not a leaderboard:

- records, placings, ratings, and sample counts are never normalized into a strength score;
- Singles and Doubles remain separate, and old regulations do not silently enter the current evidence pool;
- species-only rows can support co-occurrence facts but cannot masquerade as complete representative sets;
- standard-set caches are low-confidence references; SP filled from metagame data is an estimate rather than an observed spread, while the user's actual team is recalculated from its own set;
- the model may study complete observed teams, but it must not silently return one as “the recommendation.” A verbatim adoption must disclose the sample overlap, why it fits, and which modifications were considered.

### Reading Team Results

**Legality is not a guess with two outcomes.** `valid` means the available facts passed deterministic checks; `invalid` includes actionable errors; `unknown` means the input or factual base is incomplete and must not be presented as legal.

**Usage is not strength.** Rankings, real-team co-occurrence, and event tags are separate views. The model may recommend after explaining trade-offs, but the tools do not blend those facts into an opaque score.

**A CHECK label is not a battle simulator.** C2/C1/C0 describe switch-in, same-field/revenge, or no-clean-line relationships under the evaluated sets, damage, Speed, and turn budget. Setup, recovery, status, PP, and long-term play are only surfaced within the tool's stated limits.

**Confidence belongs to the conclusion.** Real joint sets, marginal fallbacks, standard-set caches, thin samples, and partial calculation failures carry different confidence and caveats. Important assumptions should remain visible in the final answer.

### Example Requests

```text
Start the team-building flow for a current-rule Doubles team and ask me what you need to know.

Build one final Singles team around Mega Staraptor, and explain the important trade-offs.

Here is my Showdown team. Check legality, structural gaps, and matchups into the current top 20, then propose revisions.

Build Doubles using only the Pokémon in roster.txt. Keep Gengar and do not use Trick Room.

How should these six be selected into four? Which selection axis changes by opponent?

Tune my Incineroar to always survive the specified Garchomp Earthquake, and show what SP must be sacrificed.

Compare replacing member A with B across defense, speed control, and metagame holes—not just popularity.

Find real-team structures containing these two anchors, summarize the common patterns and variants, and do not copy a full team.
```

## How the Skills Work Together

A single request may invoke several skills, but each factual responsibility stays explicit:

```text
free-form user request
  ├─ names, forms, learnsets, item legality ─────> dex
  ├─ current usage and configuration marginals ─> meta
  ├─ damage and Speed under defined conditions ─> calculator
  └─ team intent, candidates, diagnosis, tuning ─> team
                                                 └─ calls the other three as needed
```

For example, “build a Doubles team around Mega Metagross and make it survive Earthquake from a common Life Orb Garchomp” needs name and legality facts, current usage, real-team structures, candidate evaluation, and an exact survival threshold. “Can Bullet Punch from the current metagame's standard Mega Metagross set OHKO a common Alolan Ninetales set?” combines the dex, meta, and calculator skills but does not enter the team workflow.

## Quark Drive Web

The companion front end turns the facts and calculations supplied by the four skills into a workspace
that is easier to browse, compare, and verify. It is useful for quickly surveying the metagame,
researching a Pokemon, comparing damage and Speed in batches, checking standard or custom matchups,
and carrying team-analysis results into other pages for verification. Zh/en/ja names are localized
only at the display boundary. The current season, rule, and snapshot date appear at the top of the
page; use that version information when interpreting metagame results.

If an agent with shell access is already set up in this project, you can ask it to download, install and
start the local edition in one sentence — see "Method A — Ask an Agent to Set It Up" below. The agent
follows the instructions in `AGENTS.md` / `CLAUDE.md` under "Local Visual Interface".

| Capability | Online | Local bundle |
|---|---|---|
| Singles/Doubles rankings, details, KO panels, separate usage/KO histories, and Top Meta counts from real teams compared with official data | Yes | Yes |
| Pokémon, move, item, and ability dex with zh/en/ja search | Yes | Yes |
| Batch damage, rolls, KO ranges, and Speed lines | In-browser | In-browser |
| Calculator search/filter drawer with observed builds, Meta fallback, and a blank Custom option | Yes | Yes |
| Standard-set matrix and actual-team KO/CHECK grid | Yes | Yes |
| Quick survival calculation | Yes | Yes |
| My teams, Pokémon Box, and calculation history | Saved in this browser | Saved in the local workspace folder |
| Exact `team.tune` cliff analysis | Enabled features and service workload limits | Yes |
| One-shot Q&A, simplified builder, diagnosis, optional AI explanation | Enabled AI features and daily limits | Replaced by the complete local-agent workflow |
| UEP session/artifact panel | Not supported | Yes, for collaboration with a local AI assistant already working on the task |

The online builder is a fixed-form, single-result workflow. It does not replace the full local UEP
conversation, candidate comparison, revision loop, or file collaboration.

The calculator drawer expands one Pokémon's build cards at a time. Its destination switch fills the
selected side; Speed lines support up to six configured Pokémon on each side, and quick bulk tuning
supports up to six incoming attackers. A full side rejects an additional card with an explicit notice.
Environment KO target and threat lists are **rankings, not usage percentages**. Their capture time and
coverage can differ from usage history; an uncollected axis is shown as a data gap.

### Use the online edition

Open [https://champion.mpan.top](https://champion.mpan.top). Static environment and dex
browsing plus browser calculations require no account. Q&A, builder, diagnosis, and AI explanation are
shown only when the corresponding features are enabled; quota status comes from the service.
Standard-set matchup pages are low-confidence references. Paste or enter the actual set when judging
your own team, and carry generated/diagnosed teams into the calculator for verification.

### Download the prebuilt local edition

Download `Quark_Drive_Web.zip` from the
[`data-latest` release](https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest).
The bundle binds one SPA, projection, bridge, four skills, runtime configuration, and manifest. It
requires Python 3.10+ and the locked `requirements-runtime.txt`, but not Node.js.

#### Method A — Ask an Agent to Set It Up (Recommended)

If an agent with shell access is already in this project (because you installed the skills with the
agent-assisted installation method, or you placed this repo's `AGENTS.md` / `CLAUDE.md` in the project
directory), just ask it directly:

> Download the prebuilt local visual interface from the `data-latest` release of `pmwl0128/quark_drive_skills`, install its `requirements-runtime.txt`, and start it. Then give me the local URL.

The agent fetches the zip, extracts it, installs the runtime deps, launches the platform start script,
and returns the one-time `http://127.0.0.1:<port>/#bootstrap=...` URL. The full step list it follows is
in `AGENTS.md` / `CLAUDE.md` under "Local Visual Interface", so you do not have to remember the commands.

#### Method B — Manual

After extracting the zip and entering the front-end directory:

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

Open the complete one-time `http://127.0.0.1:<port>/#bootstrap=...` URL printed by the launcher. The
service listens on loopback by default. Set `PCUI_LOCAL_PORT` in
`~/.pokemon-champions/pc.env` to change the default port. Each sign-in link works once: for another
browser, or after a restarted service disconnects an open page, run `python bridge/pcui open` in the
unpacked folder for a fresh link without restarting.

To let an AI assistant work on sessions created in the local UI, run `python bridge/pcui install-agent`
once (add `--target codex` for Codex). The local home page's Get started card shows the exact command
for your machine. Then send the handoff instruction from a session page to the assistant; its progress
appears in the page as it works. Answer its questions and checkpoint decisions right on the session
page; a waiting assistant picks them up and continues on its own. Assistants with MCP support can
connect through `python bridge/pcui mcp` instead; `python bridge/pcui mcp --print-config` prints the
registration for Claude Code and Codex.

### Build from source

Use Python 3.10+ and the Node.js version used by release verification (currently Node.js 22). The
platform `build-ui` script runs `npm ci`, creates the projection from the checked-out skills, and
performs one Vite build; the matching `start-ui` script launches it. Do not combine `dist`, projection,
bridge, or skill trees from different snapshots—the identity checks reject that assembly.

```powershell
git clone https://github.com/pmwl0128/quark_drive_skills.git
Set-Location .\quark_drive_skills
python -m pip install -r frontend/requirements-ui.txt
.\frontend\build-ui.ps1
.\frontend\start-ui.ps1
```

On Linux/macOS use `frontend/build-ui.sh` and `frontend/start-ui.sh` after making them executable.

### Privacy and updates

- Local mode listens on `127.0.0.1` and needs no hosted-model key for normal data and calculation tools.
  Teams, Pokémon, results and AI sessions live in a workspace folder: `~/.pokemon-champions/workspace` for
  the prebuilt bundle, `storage` under the launch directory for a source checkout. Change or move it on the
  Storage page; to back it up, stop the local service and copy the whole folder.
- Online Q&A, builder, diagnosis, actual-team matchup, or AI explanation sends the corresponding input
  to the online service. Keep private rosters and review notes in the local bundle if they should not be
  submitted.
- To update the prebuilt edition, download the complete rolling zip into a new directory and verify it
  before removing the previous copy. The workspace is outside the unpacked folder, so removing the old
  copy keeps your data; if you ever placed the workspace inside an old copy, move it on the Storage page
  first. For a source checkout, pull and rerun the platform build/start
  scripts; do not overwrite only selected generated subdirectories.

## Installation

All four skills are installed together. No command registration is required afterward; the agent selects a skill from its `SKILL.md` description.

### Method A: Ask an Agent to Install It (Recommended)

Give the following request to an agent with shell access:

> Globally install all four skills from https://github.com/pmwl0128/quark_drive_skills. Also install the `quickjs-ng>=0.15` dependency; if that cannot be installed in this environment, confirm that the Node.js fallback works. Finally, confirm that all four `SKILL.md` files are discoverable and run the calculator's `schema` command once.

This is the recommended default: the agent can identify the host, choose the correct directory, handle the runtime dependency, and verify the copied skills without asking the user to reason about paths or link behavior.

### Method B: Claude Code Plugin Marketplace

Enter these commands inside Claude Code. If you installed the old plugin, uninstall it first with `/plugin uninstall pokemon-champions@pmwl`:

```text
/plugin marketplace add pmwl0128/quark_drive_skills
/plugin install quark-drive-skills@quark-drive
```

### Method C: One Command

Install [Node.js](https://nodejs.org/) first, then run this in your project directory:

```bash
npx skills add pmwl0128/quark_drive_skills
```

For a global installation available to every project:

```bash
npx skills add pmwl0128/quark_drive_skills -g
```

The installer is provided by [vercel-labs/skills](https://github.com/vercel-labs/skills) and prompts for the target agent and scope. The current link-based installation path may fail on Windows; choose its copy-install option instead, or create the links manually.

### Method D: Manual Copy

```bash
git clone https://github.com/pmwl0128/quark_drive_skills.git
mkdir -p ~/.claude/skills ~/.agents/skills
cp -r quark_drive_skills/.claude/skills/* ~/.claude/skills/   # Claude Code
cp -r quark_drive_skills/.agents/skills/* ~/.agents/skills/   # Codex
```

The `.agents/skills/` and `.claude/skills/` trees are byte-identical mirrors. Use the one discovered by your agent.

### Requirements

- **Python 3.10+** for all four Python query entry points; normal dex, meta, and team queries use only the standard library.
- **quickjs-ng 0.15+ (recommended)** as the default in-process runtime for the NCP damage/Speed calculator; Node.js is not needed when it is installed, and the calculator skill ships its own `requirements.txt`.
- **Node.js (fallback / optional)** is used automatically by the calculator's Python entries when quickjs-ng is absent, and is required when using the Method-C `npx` installer.
- **openpyxl 3.1+ (optional)** only when running meta `export-excel` yourself; release workbooks are already included.

To install only the calculator runtime dependency directly:

```bash
python -m pip install "quickjs-ng>=0.15"
```

### Updating

| Installation | Update method |
|---|---|
| Agent-assisted | Ask the agent to reinstall all four skills from the same repository and re-check quickjs-ng / the Node fallback |
| `npx skills` | `npx skills update` |
| Claude Code plugin | `/plugin marketplace update` |
| Git / manual copy | Run `git pull`, then copy the relevant skills tree again |

### Verifying the Installation

Confirm that your skills directory contains:

```text
ncp-damage-calculator
pokemon-champions-dex
pokemon-champions-meta
pokemon-champions-team
```

Then ask “Can Garchomp learn Dragon Dance?” followed by “Build me a Singles team.” The first should route to the dex; the second should start the team's guided intake.

## Beginner Guide: Set Up a Battle Project

The skills can be installed globally, but a dedicated project directory works better for ongoing team work. It keeps rosters, sets, battle notes, and agent instructions together so later revisions retain useful context.

### 1. Create a Project Directory

```bash
mkdir my-champions
cd my-champions
```

Any directory name is fine. Open it directly in an IDE-based agent, or change into it before starting Claude Code or Codex from the command line.

### 2. Install the Four Skills

After starting the appropriate AI agent, tell your agent:

> Install all four skills from https://github.com/pmwl0128/quark_drive_skills into the current project, and copy the bundled `CLAUDE.md` and `AGENTS.md` into the project directory. Also install `quickjs-ng>=0.15`; if that cannot be installed in this environment, confirm that the Node.js fallback works. Finally, confirm that all four `SKILL.md` files are discoverable and run the calculator's `schema` command once.

You can also use any other [installation method](#installation) above.

### 3. Add Your Own Data (Optional)

Use any filenames and formats that are convenient:

```text
my-champions/
  CLAUDE.md           # optional
  AGENTS.md           # optional
  roster.md           # Pokémon you own
  current-team.txt    # Showdown export or prose set notes
  matchup-notes.md    # battle logs, recurring opponents, revision ideas
```

No file follows a required schema. Tell the agent which path to read and how it should be used—for example, “use only Pokémon from `roster.md`” or “re-evaluate this team using `matchup-notes.md`.” The agent reads and normalizes the file before calling the relevant skill.

### 4. Start Asking and Iterate

Launch the agent in the project directory and begin with any of these:

```text
Can Garchomp learn Dragon Dance?
Analyze current-team.txt for legality and metagame matchups.
Build a Singles team using only roster.md.
Revise the team around the failures in matchup-notes.md, but keep my locked anchor.
```

Keeping each team version and its battle notes gives the agent far better context than restarting from an empty conversation every time.

## Model Routing and Workflow Reliability

Automatic skill activation depends on the agent correctly interpreting `SKILL.md` and continuing to follow its instructions throughout the task. It is not a runtime guarantee that behaves identically across all models. Models with weaker tool use or long-horizon instruction following may answer from memory, call only one factual skill, or skip parts of the built-in team workflow.

When that happens, be explicit:

```text
Read pokemon-champions-team/SKILL.md first, then handle this request with the complete team workflow.
Do not answer this from memory: use dex for names and legality, meta for usage, and calculator for damage and Speed.
Follow the team skill's built-in process; do not skip candidate comparison, legality validation, or final verification.
```

Using the bundled `CLAUDE.md` or `AGENTS.md` as project instructions also improves routing and workflow consistency. These files do not grant the model stronger battle judgment; they reinforce when and how the tools should be used. If a model repeatedly ignores tool results, moving to a model with stronger tool use and long-instruction adherence is usually more effective than adding more corrective prompting.

## Usage Notes

1. Start Claude Code, Codex, or another Agent Skills-compatible agent in any project directory.
2. Ask naturally in Chinese, English, or Japanese; you do not need to memorize CLI commands.
3. Paste teams, owned rosters, and constraints into the conversation, or keep them in any local file and provide the path. There is no required filename.
4. For current-metagame claims, check the season/rule/as-of stamp at the top of this page. Update the whole skill package when you need a newer snapshot.

## Metagame Workbooks

Each release regenerates three workbooks from the complete Singles and Doubles snapshots and update reports, published under fixed names on the [data-latest release](https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest) (rolling: only the newest snapshot is kept; the git tree carries no binaries, so clones stay lean):

- [workbook_en.xlsx](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_en.xlsx) (English)
- [workbook_zh.xlsx](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_zh.xlsx) (Chinese)
- [workbook_ja.xlsx](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/workbook_ja.xlsx) (Japanese)

Each workbook uses one primary language; the Chinese and Japanese versions include canonical English names for cross-reference. Main sheets include:

| Sheet | Contents |
|---|---|
| Singles / Doubles | Rank, name, common moves, items, abilities, natures, partners, and SP distributions |
| Update report | Factual ranking, entry/drop, and configuration changes from the previous snapshot, plus linked name lookup |

The workbooks are facts-only reports and contain no AI interpretation of the metagame.

Rank-trend line charts `trend_{single,double}_{zh,ja,en}.png` (Singles / Doubles × three languages) ship on the same release. They cover the last up to 10 refreshes on a rolling basis: any Pokémon that entered the Top 30 in any period is tracked across the whole window, plus big movers that reached the top 60. The axis keeps ranks 1–30 uniform and compresses everything deeper; rising / falling / stable lines are color-coded with Pokémon names labeled at both ends.

![Singles rank trend](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/trend_single_en.png)

![Doubles rank trend](https://github.com/pmwl0128/quark_drive_skills/releases/download/data-latest/trend_double_en.png)

## Freshness and Honest Limits

- Meta and real-team data change with the environment. The release guarantees only the local snapshot identified at the top of this page; it does not claim to be live.
- Dex, meta, team samples, and calculator data are queried from the package, so normal use requires no network. Update the skill package to obtain newer data.
- Historical seasons are queryable, but an old regulation is never silently mixed into current evidence.
- Dex facts and calculator formulas may also change with a regulation. Cross-regulation questions must state the intended season/rule.
- The tools cannot cover every turn state, hidden-information branch, or player decision. Assumptions, confidence, and caveats are part of the result.

## Data Sources and Acknowledgments

The release contains transformed, validated query data and scrubbed facts-only projections. Raw development-side scrape caches are not distributed.

### Metagame Rankings and Usage

| Source | Use |
|---|---|
| [Silph Scope](https://silph-scope.com/) | Primary metagame source: rankings, move/item/ability/nature/SP/partner details, the KO relation lists (who knocks out whom), and KO move shares for both formats |
| [GameWith](https://gamewith.jp/) Pokémon Champions | Fallback metagame feed and cross-check |
| [PokeChamp DB](https://pokechamdb.com/) | Fallback metagame feed and cross-check |
| [MetaRoll](https://metaroll.app/ladder) | Fallback source for doubles KO move shares. Usage data from MetaRoll (metaroll.app) |

### Battle Dex and Names

| Source | Use |
|---|---|
| [Serebii.net](https://www.serebii.net/) Champions Pokédex | Champions learnsets, abilities, and move priority |
| [52Poke / 神奇宝贝百科](https://wiki.52poke.com/) | Chinese/Japanese/English display names, aliases, and form-specific facts |
| [PokéAPI](https://pokeapi.co/) | Independent name validation |

### Battle Calculation

| Source | Use |
|---|---|
| [NCP VGC Damage Calculator](https://github.com/nerd-of-now/NCP-VGC-Damage-Calculator) | Core entity data and damage engine |
| [smogon/damage-calc](https://github.com/smogon/damage-calc) | Independent formula oracle. Development tests replay the same cases through its `@smogon/calc` engine — reached via the independently maintained [EXO Champions calculator](https://github.com/SebNotFound/champions-calc) — and compare the rolls against the shipped engine. Neither its code nor its data is distributed here. |

### Real-Team Samples

| Source | Format | Use |
|---|---|---|
| [Yakkun / ポケモン徹底攻略](https://yakkun.com/) | Singles | Complete joint sets and SP |
| [OP.GG](https://op.gg/) Pokémon Champions | Singles | Replica teams and SP |
| [GameWith](https://gamewith.jp/) / [Game8](https://game8.jp/) | Singles | Top-build facts from completed seasons |
| [POCH](https://poch.ms/en) | Singles | Public team index and reviewed in-game export images |
| [Limitless TCG](https://play.limitlesstcg.com/) | Doubles | Online event teams, records, and placings |
| [Nimbasa City Post](https://www.nimbasacitypost.com/) | Doubles | Mirrored open team lists from official championship events |
| [VGCPastes / Pokepaste](https://pokepast.es/) | Doubles | Community-curated complete teams and SP |

Thank you to the projects, sites, tournament organizers, and community contributors who make these data and tools available. Rankings, records, and published teams are used as factual evidence only and do not imply a strength endorsement by this project.

Special thanks to [PokeChamp DB](https://pokechamdb.com/) for its strong support during skill development.

## Planned improvements

- **Workflow speed and compatibility:** reduce redundant tool calls and measure whether supported agents complete the required team workflow.
- **Turn-simulation research:** investigate multi-turn behavior in a separate experimental pipeline. The released skills and Web continue to use their documented damage, Speed, and matchup calculations.
- **Online capacity:** improve delivery and response latency, and adjust service limits using observed workload.

## License

`ncp-damage-calculator` bundles formulas from the NCP VGC Damage Calculator. Its upstream license remains in the skill's `references/upstream-LICENSE` and is documented in `NOTICE`. See `LICENSE` for the MIT terms covering the rest of this release.

> Pokémon and character names are trademarks of Nintendo / Creatures Inc. / GAME FREAK inc. This is an unofficial fan project and is neither affiliated with nor endorsed by those companies.
