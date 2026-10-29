---
name: pokemon-champions-dex
description: Offline-first Pokémon Champions battle dex lookup with trilingual aliases (Chinese / English / Japanese), batch queries, and multi-condition reverse search. Use when the request asks for a canonical dex fact about a Pokémon/form/move/ability/item/nature/name, not for popularity trends or damage arithmetic. Chinese examples include "X能学Y吗", "谁会Y", "X是什么属性/种族值/特性", "X的中文/日文/英文名", "这个名字对应谁", "这个Mega石给谁用". English examples include "can X learn Y", "who learns Y", "what are X's types/stats/abilities", "resolve this name", "which form/item is this". Japanese examples include "XはYを覚える？", "Yを覚えるポケモン", "Xのタイプ/種族値/特性", "この名前はどのポケモン？", "このメガストーンの対応先は？". Use it while building or reviewing teams whenever a factual dex claim or name normalization is needed.
---

# Pokémon Champions Dex

Query the bundled Pokémon Champions battle dex before making factual claims about a Pokémon, form,
move, ability, item, nature, or learnset. This skill answers "what exists and what can it learn?";
metagame popularity belongs to `$pokemon-champions-meta`, and damage arithmetic belongs to
`$ncp-damage-calculator`.

## Workflow

1. Use `resolve` to normalize free-form Chinese, English, or Japanese names. It is batch,
   typo-tolerant by default, understands multilingual Mega affixes, X/Y/Z qualifiers (full-width
   letters included) and spoken nicknames such as 地龙/x喷/y喷, and preserves 1:1 input order.
2. Use the entity commands or `batch` when full records are needed; they use the same
   exact → Mega composition → fuzzy order as `resolve`. `--strict` disables fuzzy guessing while
   retaining deterministic Mega composition and ambiguity refusal.
3. Use `find` / `find-move` for reverse search; use `--where` for OR, NOT, or nested conditions.
4. Prefer `--format json` for downstream processing. Use `--lang zh|ja|en` only for human-readable
   Markdown output.

## Commands

Normalize names first when the input is free-form:

```bash
python scripts/champdex.py resolve Mega耿鬼 超级雪妖女 牛蛙君 咆哮虎
python scripts/champdex.py resolve 灭亡之歌 地震 保护 --kind move
python scripts/champdex.py resolve 地面 フェアリー Fire --kind type --format json
```

Query one or many full records:

```bash
python scripts/champdex.py pokemon 姆克鹰 Mega-Staraptor 巨金怪
python scripts/champdex.py move 近身战 Close-Combat
python scripts/champdex.py ability 唱反调 Contrary
python scripts/champdex.py item 耿鬼进化石 Gengarite
python scripts/champdex.py nature 固执 Adamant
python scripts/champdex.py batch pokemon 姆克鹰 巨金怪 弃世猴 洗衣机
```

Reverse-search Pokémon or moves:

```bash
python scripts/champdex.py find move 近身战 type 飞行
python scripts/champdex.py find type 水 type 地面 stat spe>=70
python scripts/champdex.py find-move type Fire category Special power ">=90"
python scripts/champdex.py find --where '{"and":[{"type":"Dragon"},{"not":{"mega":true}}]}'
```

Names accept Chinese, English, and Japanese aliases. Exact aliases win; typo fallback is conservative,
reports its correction, and refuses ambiguous ties. Use `--strict` when validation requires exact-only
resolution. `find` and `reverse` are always exact. The full positional and boolean grammars are exposed
by the CLI schema.

## Contract And Output

Treat the executable schema as the authority for commands, fields, canonical stat keys, and errors:

```bash
python scripts/champdex.py schema
python scripts/champdex.py names --format json
```

Canonical JSON is language-independent: English `name` is the cross-skill join key; stat keys and type
values remain canonical English. Human-readable output language precedence is `--lang`, then
`POKEMON_CHAMPIONS_LANG`, then `en`.

`names` exports the official `{kind,name,name_zh,name_ja}` vocabulary for presentation and bounded
text checks. It includes Pokémon, moves, abilities, items and natures, without aliases or combat data.

Read `references/schema.md` only when database fields, name-resolution behavior, or reverse-query
semantics are relevant. It supplements the executable schema rather than replacing it.

## Boundaries

- The bundled SQLite database and JSON mirror are read-only; the skill performs no network access.
- Report dex facts and legality-relevant learnset facts, not popularity or strategic role labels.
- A valid dex lookup is not a metagame recommendation. Use the sibling skills for usage and damage.
