#!/usr/bin/env python
"""intake (UEP P3): the STATIC question catalog for the optional conversational front door.

A MENU, never a script (计划 P3: 菜单不是剧本): the catalog lists what CAN be asked, how each
answer maps onto the typed build-context / draft fields, and which context-audit gap makes a
question worth asking (`triggers_on` keys directly into the P2 gap vocabulary). Two modes
(design §19.6): STEADY-STATE refinement picks <=3 questions PER ROUND for the audit's gaps (a
per-round batch, never a total cap) and iterates; a NEW open-ended build (its intent not yet
elicited) instead completes the `guided_walk` base set once (`intake_catalog(onboarding=True)`).
WHICH questions get asked is the orchestrating AI's judgment; this module never decides flow.

Guards (pinned by tests):
- pure static emitter: it never asks, never parses free text, never converges, never recommends.
  There is NO per-question next_question_policy / flow pointer; the ONE sanctioned ordering is the
  onboarding walk's ask order, which lives in the ONBOARDING_WALK constant and surfaces only as the
  order of `guided_walk` (onboarding mode) — never as a menu-mode flow field.
- every option's maps_to lands on a LEGAL contracts field/enum (or a declared draft field) — the
  catalog cannot invent context vocabulary.
- de-jargoned: question/option text uses plain language + examples; tactic vocabulary tokens
  (stall/trickroom/...) live only in maps_to, never in the wording shown to users.
- format-aware: options tagged with `formats` apply to one battle format only (singles has no
  Tailwind tempo; doubles has no entry hazards) — `intake_catalog(game_format=...)` filters.
- trilingual: text/label/example carry zh/ja/en together; the JSON output is language-invariant
  (the --lang flag does not change this command's output, per conventions).
"""
from __future__ import annotations

import copy

from typing import Any

# maps_to shapes (an option/free_form may carry ONE mapping or a LIST of them):
#   {"target": "context", "set": {field: value}}      scalar knob
#   {"target": "context", "append": {field: token}}   list-field token (e.g. exclude_tactics)
#   {"target": "context", "remove": {field: "{slot}"}} conflict resolution: drop entries from a list
#   {"target": "context", "fields": [...]}            free-form collection into these fields
#   {"target": "draft", "set": {field: value}}        P6 draft-level flag
#   {"target": "answer-shape", "set": {"mode": ...}}  consumer-facing answer form: compare | single
#                                                     | explain_only (no operator consumes it; the
#                                                     orchestrator does)
#   {"target": "team-file"}                           a pasted team -> team-json (outside context)
#   None                                              fallback / AI-side only, nothing typed
# Conflict-resolution questions carry {placeholders} in text/values — fill them from the SAME-named
# fields of the triggering context-audit gap entry (members/member/query/suggestions/tokens/path).

CATALOG: list[dict[str, Any]] = [
    {
        "id": "format",
        "text": {
            "zh": "想打单打还是双打？没偏好的话也可以让我来选。",
            "ja": "シングルとダブル、どちらで遊びますか？こだわりがなければ、こちらで選ぶこともできます。",
            "en": "Singles or doubles? If you have no preference I can pick for you.",
        },
        "options": [
            {"id": "single", "label": {"zh": "单打", "ja": "シングル", "en": "Singles"},
             "maps_to": {"target": "context", "set": {"format": "single"}}},
            {"id": "double", "label": {"zh": "双打", "ja": "ダブル", "en": "Doubles"},
             "maps_to": {"target": "context", "set": {"format": "double"}}},
            {"id": "delegate",
             "label": {"zh": "都行，你来选", "ja": "どちらでも。任せます", "en": "Either — you pick"},
             "maps_to": {"target": "context", "set": {"format": "double"}},
             "note": {"zh": "仅在用户明确委托时才默认双打——当前双打是主流对战环境（真实队库覆盖也最厚），"
                            "但不代表双打更适合所有人；该理由必须随答案披露。",
                      "ja": "ユーザーが明示的に任せた場合のみダブルを既定にします——現在ダブルが主流環境"
                            "（実戦チームライブラリも最も厚い）ですが、誰にでもダブルが合うという意味では"
                            "ありません。この理由は回答で必ず開示します。",
                      "en": "Default to doubles ONLY on explicit delegation — doubles is the current "
                            "mainstream environment (and the richest real-team library), which does "
                            "NOT mean doubles suits everyone; disclose this reasoning in the answer."}},
        ],
        "info_value": "high",
        "triggers_on": ["blocking:format"],
        "skippable": False,                # blocking: metagames are never mixed
        "must_confirm": False,
        "default": None,                   # NO safe default — missing format blocks
    },
    {
        "id": "use_case",
        "text": {
            "zh": "这次建队主要拿来做什么？",
            "ja": "今回のチームは主に何のために組みますか？",
            "en": "What is this team mainly for?",
        },
        "options": [
            {"id": "quick_stable",
             "label": {"zh": "快速拿一支能用的稳定队", "ja": "すぐ使える安定したチームが欲しい",
                       "en": "A solid, usable team, quickly"},
             "maps_to": {"target": "context", "set": {"meta_conformance": "proven"}}},
            {"id": "around_favorite",
             "label": {"zh": "围绕我喜欢的宝可梦或主题来建", "ja": "好きなポケモンやテーマを軸に組みたい",
                       "en": "Build around a favourite Pokémon or theme"},
             "maps_to": {"target": "draft", "set": {"request_expressive": True}},
             "note": {"zh": "表达型请求：锚点问题（宝可梦锚点/主题锚点）升为必问——缺锚点即阻断，默认会抹掉本意。",
                      "ja": "自己表現型のリクエスト：アンカー質問（ポケモン/テーマ）が必須になります——"
                            "アンカー不明のままはブロッキングです。",
                      "en": "Expressive request: the anchor questions become MUST-ask — a missing "
                            "anchor is blocking (defaulting it erases the intent)."}},
            {"id": "complete_existing",
             "label": {"zh": "补完我已有的队伍", "ja": "手持ちのチームを補完したい",
                       "en": "Complete a team I already have"},
             "maps_to": {"target": "team-file"},
             "note": {"zh": "触发“起点”问题：请用户把已有队伍发来（任意格式）。",
                      "ja": "「出発点」の質問につながります：手持ちチームを送ってもらいます（形式自由）。",
                      "en": "Raises the starting-point question: ask for the existing team (any format)."}},
            {"id": "practice_meta",
             "label": {"zh": "练当前环境的常见体系", "ja": "現環境でよく見る型を練習したい",
                       "en": "Practice the current common archetypes"},
             "maps_to": {"target": "context", "set": {"meta_conformance": "proven"}}},
            {"id": "learn",
             "label": {"zh": "学建队思路，不急着要队", "ja": "チーム構築の考え方を学びたい（急がない）",
                       "en": "Learn team-building reasoning; no rush for a team"},
             "maps_to": {"target": "answer-shape", "set": {"mode": "explain_only"}},
             "note": {"zh": "答案形态改为多视图详解、不收敛到具体队。",
                      "ja": "回答は収束させず、複数視点の解説中心にします。",
                      "en": "Answer shape becomes multi-view explanation; no convergence to one team."}},
            {"id": "off_meta",
             "label": {"zh": "想用使用率偏低的冷门队", "ja": "使用率の低いマイナー寄りのチームを使いたい",
                       "en": "A deliberately off-meta, low-usage team"},
             "maps_to": {"target": "context", "set": {"meta_conformance": "off_meta"}}},
        ],
        "info_value": "high",
        "triggers_on": ["safe_default:anchor", "safe_default:meta_conformance",
                        "safe_default:style_lean"],
        "skippable": False,                # the entry question for a vague request
        "must_confirm": False,
        "default": None,
        "note": {"zh": "只确定意图与后续问题的优先级，不一次性锁死所有旋钮——本题写入的预设值全部可被后续问题覆盖。",
                 "ja": "意図と後続質問の優先度を決めるだけで、ノブを一括固定しません——ここで入る既定値は"
                       "後続の質問で上書きできます。",
                 "en": "Sets intent + follow-up priority only, never all knobs at once — presets "
                       "written here are overridable by later questions."},
    },
    {
        "id": "anchor_pokemon_or_mega",
        "text": {
            "zh": "有没有必须带、或尽量想带的宝可梦？想用某只的 Mega 形态也一起说。",
            "ja": "必ず入れたい、またはできれば入れたいポケモンはいますか？メガシンカさせたい場合も教えてください。",
            "en": "Any Pokémon you MUST bring, or would like to bring? Mention if you want its Mega form.",
        },
        "free_form": {
            "resolvers": ["dex:pokemon"],
            "maps_to": {"target": "context", "fields": ["locked", "prefer", "keep_mega"]},
            "example": {"zh": "例：“必须带耿鬼，想用它的 Mega；最好再带一只会点火的” → locked=[耿鬼]，"
                              "keep_mega=耿鬼，prefer=[会鬼火的成员]",
                        "ja": "例：「ゲンガーは必須でメガ前提、できれば鬼火要員も」→ locked=[ゲンガー]、"
                              "keep_mega=ゲンガー、prefer=[鬼火要員]",
                        "en": "e.g. \"Gengar is a must, Mega it; ideally someone with Will-O-Wisp\" -> "
                              "locked=[Gengar], keep_mega=Gengar, prefer=[a Will-O-Wisp user]"},
        },
        "options": [
            {"id": "none", "label": {"zh": "没有，交给你", "ja": "特にない。任せます",
                                     "en": "None — up to you"},
             "maps_to": None},
        ],
        "info_value": "high",
        "triggers_on": ["safe_default:anchor", "blocking:anchor_expressive"],
        "skippable": True,                 # NOT skippable when the request is expressive
        "must_confirm": True,              # hard locks / fuzzy names / Mega forms echo back
        "default": {"value": "无锚点（按环境建）", "source": "protocol",
                    "disclose": True},
    },
    {
        "id": "anchor_theme_or_gameplan",
        "text": {
            "zh": "有没有想围绕的打法或主题？",
            "ja": "軸にしたい戦い方やテーマはありますか？",
            "en": "Any game plan or theme you want to build around?",
        },
        "free_form": {
            "resolvers": ["tactic_classifier", "dex:pokemon"],
            # giving a theme IS an expressive anchor: the draft flag rides the SAME mapping so a
            # maps_to-only consumer cannot lose the P6 escalation (external audit 2026-07-03).
            "maps_to": [{"target": "context", "fields": ["wants", "prefer"]},
                        {"target": "draft", "set": {"request_expressive": True}}],
            "example": {"zh": "例：“想玩雨天队” → wants=[weather]；“想玩空间慢速反打” → wants=[trickroom]；"
                              "“想围绕某两只的组合” → prefer=[那两只]",
                        "ja": "例：「雨パで遊びたい」→ wants=[weather]；「トリルの遅速逆転」→ "
                              "wants=[trickroom]；「この2匹のコンビ軸」→ prefer=[その2匹]",
                        "en": "e.g. \"a rain team\" -> wants=[weather]; \"Trick Room pace\" -> "
                              "wants=[trickroom]; \"around this duo\" -> prefer=[the two]"},
        },
        "options": [
            {"id": "none", "label": {"zh": "没有特定主题", "ja": "特にテーマはない", "en": "No particular theme"},
             "maps_to": None},
        ],
        "info_value": "high",
        "triggers_on": ["safe_default:anchor"],
        "skippable": True,
        "must_confirm": True,
        "default": {"value": "无主题锚点", "source": "protocol", "disclose": True},
        "note": {"zh": "非宝可梦锚点：打法/主题落在 wants（组装时尊重的意图字段），组合成员落 prefer/locked；"
                       "选择“围绕主题”的表达型请求同时标记 request_expressive（draft 层）。"
                       "真正玩法向（gimmick）的独立建模仍待议——涉及时如实告知边界。",
                 "ja": "ポケモン以外のアンカー：戦法/テーマは wants に、コンビは prefer/locked に入ります。"
                       "表現型の場合は request_expressive（draft 側）も立てます。ギミック軸の本格的な"
                       "モデリングは未定です——該当時は正直にその旨を伝えます。",
                 "en": "The non-Pokémon anchor: plans/themes land in `wants` (honored when composing), "
                       "combo members in prefer/locked; expressive theme requests also mark "
                       "request_expressive (draft level). True gimmick-anchor modeling is still an "
                       "open item — say so honestly when it comes up."},
    },
    {
        "id": "starting_point",
        "text": {
            "zh": "是从零开始建，还是已经有队伍（或半成品）要我补，还是只能从固定的一批宝可梦里选？",
            "ja": "ゼロから組みますか？既にあるチーム（未完成でも）を補完しますか？それとも決まった手持ちの中からだけ選びますか？",
            "en": "Build from scratch, complete an existing (partial) team, or pick only from a fixed pool?",
        },
        "options": [
            {"id": "from_scratch", "label": {"zh": "从零开始", "ja": "ゼロから", "en": "From scratch"},
             "maps_to": None},
            {"id": "have_team",
             "label": {"zh": "我有队伍要补", "ja": "補完してほしいチームがある", "en": "I have a team to complete"},
             "maps_to": {"target": "team-file"},
             "note": {"zh": "请用户把队伍发来（截图/文字/导出格式都行）——由 AI 转写成 team-json 随 context 提交；"
                            "skill 不读自由文本。",
                      "ja": "チームを送ってもらいます（スクショ/テキスト/エクスポート可）——AI が team-json に"
                            "変換して context と一緒に渡します。",
                      "en": "Ask for the team in ANY format (screenshot/text/export) — the AI transcribes "
                            "it into team-json alongside the context; the skill never parses free text."}},
            {"id": "have_pool",
             "label": {"zh": "只能用固定的一批宝可梦", "ja": "決まった手持ちの中からだけ",
                       "en": "Only from a fixed pool I own"},
             "maps_to": {"target": "context", "set": {"owned_only": True}},
             "note": {"zh": "把持有池发来（任意格式）→ owned[]。注意：待补的队伍与持有池是两回事，不要混填。",
                      "ja": "手持ちプールを送ってください（形式自由）→ owned[]。補完対象チームと手持ちプールは"
                            "別物です。混ぜないでください。",
                      "en": "Collect the pool (any format) -> owned[]. A team-to-complete and an owned "
                            "pool are different things — never mix them."}},
        ],
        "free_form": {
            "resolvers": ["team:parse", "dex:pokemon"],
            "maps_to": {"target": "context", "fields": ["owned"]},
            "example": {"zh": "例（持有池）：“我有耿鬼、烈咬陆鲨、仙子伊布…” → owned=[…]",
                        "ja": "例（手持ち）：「ゲンガー、ガブリアス、ニンフィア…」→ owned=[…]",
                        "en": "e.g. (pool) \"I have Gengar, Garchomp, Sylveon…\" -> owned=[…]"},
        },
        "info_value": "high",
        "triggers_on": ["blocking:owned"],
        "skippable": True,
        "must_confirm": True,              # only ambiguities / fuzzy corrections echo back
        "default": {"value": "从零开始（不限池）", "source": "protocol", "disclose": False},
    },
    {
        "id": "availability_and_avoid",
        "text": {
            "zh": "有没有绝对不用、或者尽量不想用的宝可梦或道具？另外有没有你不想围绕着打的风格？",
            "ja": "絶対に使わない、またはなるべく避けたいポケモンや道具はありますか？軸にしたくない戦い方はありますか？",
            "en": "Anything you absolutely won't use — or would rather avoid — Pokémon or items? Any style you don't want to build around?",
        },
        "free_form": {
            "resolvers": ["dex:pokemon", "dex:item", "tactic_classifier"],
            "maps_to": {"target": "context", "fields": ["avoid", "avoid_soft"]},
            "example": {"zh": "例：“绝对不用讲究围巾” → avoid=[讲究围巾]；“尽量别用耿鬼” → avoid_soft=[耿鬼]",
                        "ja": "例：「こだわりスカーフは絶対なし」→ avoid=[こだわりスカーフ]；"
                              "「ゲンガーはなるべく避けたい」→ avoid_soft=[ゲンガー]",
                        "en": "e.g. \"never Choice Scarf\" -> avoid=[Choice Scarf]; \"rather not Gengar\" -> "
                              "avoid_soft=[Gengar]"},
        },
        # Styles the user may not want to BUILD AROUND — neutral wording, never framing a common
        # tactic as bad; the enum tokens live only in maps_to (de-jargoned).
        "options": [
            {"id": "stall",
             "label": {"zh": "消耗队（靠耐久和资源交换慢慢取胜）", "ja": "受け寄りの消耗戦型",
                       "en": "Attrition play (winning slowly on bulk and resource trades)"},
             "maps_to": {"target": "context", "append": {"exclude_tactics": "stall"}}},
            {"id": "trickroom",
             "label": {"zh": "空间队（反转先手权的慢速节奏）", "ja": "トリックルーム軸（遅さを先手に変える）",
                       "en": "Trick Room pace (slow speed turned into initiative)"},
             "maps_to": {"target": "context", "append": {"exclude_tactics": "trickroom"}}},
            {"id": "weather",
             "label": {"zh": "天气队（围绕天气展开）", "ja": "天候軸", "en": "Weather-centred play"},
             "maps_to": {"target": "context", "append": {"exclude_tactics": "weather"}}},
            {"id": "tailwind", "formats": ["double"],
             "label": {"zh": "顺风节奏（双打的先手权工具）", "ja": "おいかぜテンポ（ダブルの先手ツール）",
                       "en": "Tailwind tempo (the doubles initiative tool)"},
             "maps_to": {"target": "context", "append": {"exclude_tactics": "tailwind"}}},
            {"id": "screens",
             "label": {"zh": "双墙开局（光墙/反射壁辅助）", "ja": "壁貼り展開（リフレクター/ひかりのかべ）",
                       "en": "Screens openings (Reflect / Light Screen support)"},
             "maps_to": {"target": "context", "append": {"exclude_tactics": "screens"}}},
            {"id": "pivot",
             "label": {"zh": "频繁换人的轮转打法", "ja": "サイクル寄り（交代を多用）",
                       "en": "Pivot-heavy rotation play"},
             "maps_to": {"target": "context", "append": {"exclude_tactics": "pivot"}}},
            {"id": "setup",
             "label": {"zh": "先强化再输出的打法", "ja": "積んでから殴る型",
                       "en": "Set-up-then-sweep play"},
             "maps_to": {"target": "context", "append": {"exclude_tactics": "setup"}}},
            {"id": "hazards", "formats": ["single"],
             "label": {"zh": "撒钉子（入场消耗，单打常见）", "ja": "設置技軸（ステルスロック等、シングルで定番）",
                       "en": "Entry hazards (chip on switch-in; a singles staple)"},
             "maps_to": {"target": "context", "append": {"exclude_tactics": "hazards"}}},
            {"id": "none", "label": {"zh": "都行，没有忌口", "ja": "特にない", "en": "Anything goes"},
             "maps_to": None},
        ],
        "info_value": "low",
        "triggers_on": [],
        "skippable": True,
        "must_confirm": True,              # hard avoid entries echo back
        "default": {"value": "无限制", "source": "protocol", "disclose": False},
    },
    {
        "id": "play_posture",
        "text": {
            "zh": "你更喜欢主动制造击杀压力，攻守转换灵活轮换，还是优先容错和资源交换？",
            "ja": "積極的に倒しに行くのと、攻守を切り替えて回すのと、堅実に受けて資源交換で勝つの、どれが好みですか？",
            "en": "Do you prefer actively creating KO pressure, flexible offense-defense rotation, or prioritising safety margins and resource trades?",
        },
        "options": [
            {"id": "offense", "label": {"zh": "主动制造击杀压力", "ja": "積極的に倒しに行く",
                                        "en": "Actively create KO pressure"},
             "maps_to": {"target": "context", "set": {"style_lean": "offense"}}},
            {"id": "balance", "label": {"zh": "攻守转换灵活轮换", "ja": "攻守を切り替えて回す",
                                        "en": "Flexible offense-defense rotation"},
             "maps_to": {"target": "context", "set": {"style_lean": "balance"}}},
            {"id": "defense", "label": {"zh": "优先容错和资源交换", "ja": "堅実さと資源交換を優先",
                                        "en": "Prioritise safety margins and resource trades"},
             "maps_to": {"target": "context", "set": {"style_lean": "defense"}}},
            {"id": "unsure", "label": {"zh": "没概念，看着办", "ja": "こだわりなし。任せます",
                                       "en": "No idea — your call"},
             "maps_to": None,
             "note": {"zh": "不设透镜：AI 直接读结构事实，并在答案里披露未设姿态。",
                      "ja": "レンズなし：構造ファクトをそのまま読み、姿勢未設定である旨を回答で開示します。",
                      "en": "No lens: read the structural facts directly and disclose that no posture "
                            "was set."}},
        ],
        "info_value": "high",
        "triggers_on": ["safe_default:style_lean"],
        "skippable": True,
        "must_confirm": False,
        "default": {"value": "不设透镜", "source": "protocol", "disclose": True},
    },
    {
        "id": "answer_shape",
        "text": {
            "zh": "要我给出一支最符合要求的，还是几支不同取向的候选对比着挑？",
            "ja": "一番条件に合う 1 チームに絞りますか？それとも方向性の違う候補を並べて選びますか？",
            "en": "One best-fitting team, or 2-3 differently-slanted candidates to compare?",
        },
        "options": [
            {"id": "compare", "label": {"zh": "给我 2–3 支对比着挑（默认）", "ja": "2〜3 案を比較したい（既定）",
                                        "en": "2-3 candidates to compare (default)"},
             "maps_to": {"target": "answer-shape", "set": {"mode": "compare"}}},
            {"id": "single_one", "label": {"zh": "就要一支最合适的", "ja": "1 チームに絞ってほしい",
                                           "en": "Just the one best fit"},
             "maps_to": [{"target": "draft", "set": {"single_team_requested": True}},
                         {"target": "answer-shape", "set": {"mode": "single"}}]},
        ],
        "info_value": "low",
        "triggers_on": [],
        "skippable": True,
        "must_confirm": False,
        "default": {"value": "2–3 支互异候选", "source": "protocol", "disclose": False},
    },
    {
        "id": "extra_requirements",
        "text": {
            "zh": "还有没有其他要求？比如要重点针对的对手，或一定要做到的硬指标。",
            "ja": "ほかに要望はありますか？特に対策したい相手や、必ず満たしたい数値条件など。",
            "en": "Anything else? Specific opponents to target, or hard benchmarks that must hold?",
        },
        "free_form": {
            "resolvers": ["benchmark_parser", "dex:pokemon", "dex:move"],
            "maps_to": {"target": "context", "fields": ["benchmarks"]},
            "example": {"zh": "例：“一定要比烈咬陆鲨快”、“要扛住耿鬼的暗影球” → benchmarks=[outspeed vs 烈咬陆鲨, "
                              "survive 耿鬼的暗影球]",
                        "ja": "例：「ガブリアスより速く」「ゲンガーのシャドーボールを耐える」→ benchmarks=[…]",
                        "en": "e.g. \"must outspeed Garchomp\", \"must survive Gengar's Shadow Ball\" -> "
                              "benchmarks=[outspeed vs Garchomp, survive Shadow Ball]"},
        },
        "options": [
            {"id": "none", "label": {"zh": "没有了", "ja": "特にない", "en": "Nothing else"},
             "maps_to": None,
             "note": {"zh": "不设硬指标时用 validate + matchup 全表兜底。",
                      "ja": "ハード条件なしの場合は validate + matchup で下支えします。",
                      "en": "Without hard benchmarks, validate + the full matchup table backstop."}},
        ],
        "info_value": "high",
        "triggers_on": ["safe_default:benchmarks", "blocking:benchmarks.vs"],
        "skippable": True,
        "must_confirm": True,              # each parsed benchmark echoes back
        "default": {"value": "不设硬指标（validate+matchup 兜底）", "source": "protocol",
                    "disclose": True},
    },
]


# --------------------------------------------------------------------------- conflict resolution
# One TEMPLATE question per context-audit conflict kind — closing the P2->P3 join for the conflict
# level too (external audit 2026-07-03: the audit note promised the catalog answers every gap
# trigger, but conflict:* had no entries). {placeholders} fill from the triggering gap's
# same-named fields; these are confirm-style questions: wording already precise, not skippable.
_CONFLICT_COMMON = {"info_value": "high", "skippable": False, "must_confirm": False,
                    "default": None}

CONFLICT_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "conflict_prefer_and_avoid",
        "text": {"zh": "{members} 同时出现在“想带”和“避开”里——以哪边为准？",
                 "ja": "{members} が「入れたい」と「避けたい」の両方にあります——どちらを優先しますか？",
                 "en": "{members} appears in both 'prefer' and 'avoid' — which one stands?"},
        "options": [
            {"id": "keep_prefer", "label": {"zh": "想带，别避开", "ja": "入れたい方を優先",
                                            "en": "Keep it — drop the avoid"},
             "maps_to": {"target": "context", "remove": {"avoid": "{members}"}}},
            {"id": "keep_avoid", "label": {"zh": "避开，不带了", "ja": "避けたい方を優先",
                                           "en": "Avoid it — drop the prefer"},
             "maps_to": {"target": "context", "remove": {"prefer": "{members}"}}},
        ],
        "triggers_on": ["conflict:prefer_and_avoid"], **_CONFLICT_COMMON,
    },
    {
        "id": "conflict_locked_and_avoid",
        "text": {"zh": "{members} 被锁定必带，但也在避开列表里——以哪边为准？",
                 "ja": "{members} は必須指定ですが、回避リストにも入っています——どちらを優先しますか？",
                 "en": "{members} is locked but also on the avoid list — which one stands?"},
        "options": [
            {"id": "keep_locked", "label": {"zh": "必带，别避开", "ja": "必須を優先",
                                            "en": "Keep the lock — drop the avoid"},
             "maps_to": {"target": "context", "remove": {"avoid": "{members}"}}},
            {"id": "keep_avoid", "label": {"zh": "避开，解除锁定", "ja": "回避を優先",
                                           "en": "Avoid it — unlock"},
             "maps_to": {"target": "context", "remove": {"locked": "{members}"}}},
        ],
        "triggers_on": ["conflict:locked_and_avoid"], **_CONFLICT_COMMON,
    },
    {
        "id": "conflict_locked_not_owned",
        "text": {"zh": "锁定的 {members} 不在你的持有列表里——补录持有、放开“只用已有”，还是换掉它？",
                 "ja": "必須指定の {members} が手持ちにありません——追加しますか？「手持ち限定」を外しますか？差し替えますか？",
                 "en": "Locked {members} is not in your owned pool — add it, lift owned-only, or swap it?"},
        "options": [
            {"id": "add_owned", "label": {"zh": "其实我有，补录", "ja": "実は持っています。追加で",
                                          "en": "I do own it — add it"},
             "maps_to": {"target": "context", "append": {"owned": "{members}"}}},
            {"id": "lift_owned_only", "label": {"zh": "不限已有了", "ja": "手持ち限定をやめる",
                                                "en": "Lift the owned-only restriction"},
             "maps_to": {"target": "context", "set": {"owned_only": False}}},
            {"id": "unlock", "label": {"zh": "换掉它", "ja": "差し替える", "en": "Swap it out"},
             "maps_to": {"target": "context", "remove": {"locked": "{members}"}}},
        ],
        "triggers_on": ["conflict:locked_not_owned"], **_CONFLICT_COMMON,
    },
    {
        "id": "conflict_keep_mega_not_in_pool",
        "text": {"zh": "想保留 Mega 的 {member} 不在队伍/可用池里——把它加进来，还是换一个 Mega 人选？",
                 "ja": "メガ枠の {member} がチーム/プールにいません——加えますか？別のメガ枠にしますか？",
                 "en": "Your Mega pick {member} is not on the team/pool — add it, or pick another Mega?"},
        "options": [
            {"id": "lock_it", "label": {"zh": "加进来（必带）", "ja": "加える（必須）",
                                        "en": "Add it (locked)"},
             "maps_to": {"target": "context", "append": {"locked": "{member}"}}},
            {"id": "change_keep", "label": {"zh": "换个 Mega 人选", "ja": "別のメガ枠にする",
                                            "en": "Pick another Mega"},
             "maps_to": None,
             "note": {"zh": "追问新的 keep_mega（dex 解析）。", "ja": "新しい keep_mega を聞き直します。",
                      "en": "Re-ask for the new keep_mega (dex-resolved)."}},
        ],
        "triggers_on": ["conflict:keep_mega_not_in_pool"], **_CONFLICT_COMMON,
    },
    {
        "id": "conflict_keep_mega_with_none_posture",
        "text": {"zh": "你既指定保留 Mega 人选 {member}，又要求队伍不登记 Mega——以哪边为准？",
                 "ja": "メガ枠 {member} を残す指定と、メガを登録しない指定が両方あります——どちらを優先しますか？",
                 "en": "You asked to keep Mega option {member} but also register no Mega options — which one stands?"},
        "options": [
            {"id": "keep_mega", "label": {"zh": "保留 Mega 人选", "ja": "メガ枠を残す",
                                             "en": "Keep the Mega option"},
             "maps_to": {"target": "context", "set": {"mega_posture": "environment"}}},
            {"id": "keep_none", "label": {"zh": "不登记 Mega", "ja": "メガを登録しない",
                                             "en": "Register no Mega options"},
             "maps_to": {"target": "context", "set": {"keep_mega": None}}},
        ],
        "triggers_on": ["conflict:keep_mega_with_none_posture"], **_CONFLICT_COMMON,
    },
    {
        "id": "conflict_benchmark_member_not_in_team",
        "text": {"zh": "硬指标指向的 {member} 不在队上——改成队上的成员，还是删掉这条指标？",
                 "ja": "数値条件の対象 {member} がチームにいません——別のポケモンに変えますか？削除しますか？",
                 "en": "A benchmark targets {member}, who is not on the team — retarget it or drop it?"},
        "free_form": {
            "resolvers": ["dex:pokemon", "benchmark_parser"],
            "maps_to": {"target": "context", "fields": ["benchmarks"]},
            "example": {"zh": "例：“改成给耿鬼调” → 该条 benchmark 的 member 换为耿鬼",
                        "ja": "例：「ゲンガー向けに変えて」→ その条件の member をゲンガーに",
                        "en": "e.g. 'retune it for Gengar' -> that benchmark's member becomes Gengar"},
        },
        "options": [
            {"id": "drop", "label": {"zh": "删掉这条", "ja": "その条件を削除", "en": "Drop that benchmark"},
             "maps_to": {"target": "context", "remove": {"benchmarks": "{path}"}}},
        ],
        "triggers_on": ["conflict:benchmark_member_not_in_team"], **_CONFLICT_COMMON,
    },
    {
        "id": "conflict_replace_member_not_in_team",
        "text": {"zh": "要替换的 {member} 不在队上——你想换掉的是哪一只？",
                 "ja": "入れ替え対象の {member} がチームにいません——どのポケモンを外したいですか？",
                 "en": "The replace target {member} is not on the team — which member did you mean?"},
        "free_form": {
            "resolvers": ["dex:pokemon"],
            "maps_to": {"target": "context", "fields": ["replace"]},
            "example": {"zh": "例：“换掉洛托姆” → replace.member=洛托姆（清洗形态）",
                        "ja": "例：「ロトムを外す」→ replace.member=ロトム（ウォッシュ）",
                        "en": "e.g. 'swap out Rotom' -> replace.member=Rotom-Wash"},
        },
        "options": [
            {"id": "drop", "label": {"zh": "不换了", "ja": "入れ替えはやめる", "en": "Never mind the swap"},
             "maps_to": {"target": "context", "remove": {"replace": "{member}"}}},
        ],
        "triggers_on": ["conflict:replace_member_not_in_team"], **_CONFLICT_COMMON,
    },
    {
        "id": "conflict_need_unsolvable",
        "text": {"zh": "“{tokens}” 不在可用的类型/角色词表里——换个说法描述这个缺口？",
                 "ja": "「{tokens}」は使える型/役割語彙にありません——別の言い方で教えてください。",
                 "en": "'{tokens}' is not a usable type/role token — can you rephrase the gap?"},
        "free_form": {
            "resolvers": ["tactic_classifier"],
            "maps_to": {"target": "context", "fields": ["need"]},
            "example": {"zh": "例：“想要能扛水的” → need.resist=[Water]",
                        "ja": "例：「みず技を受けられる枠」→ need.resist=[Water]",
                        "en": "e.g. 'something that takes Water hits' -> need.resist=[Water]"},
        },
        "options": [
            {"id": "drop", "label": {"zh": "这条不要了", "ja": "その条件は外す", "en": "Drop that need"},
             "maps_to": {"target": "context", "remove": {"need": "{path}"}}},
        ],
        "triggers_on": ["conflict:need_unsolvable"], **_CONFLICT_COMMON,
    },
    {
        "id": "conflict_ambiguous_name",
        "text": {"zh": "“{query}” 有多个可能：{suggestions}——你指的是哪一个？",
                 "ja": "「{query}」には候補が複数あります：{suggestions}——どれのことですか？",
                 "en": "'{query}' matches several: {suggestions} — which one did you mean?"},
        "free_form": {
            "resolvers": ["dex:pokemon"],
            "maps_to": {"target": "context",
                        "fields": ["locked", "prefer", "owned", "avoid", "avoid_soft"]},
            "example": {"zh": "例：回答“水洛托姆” → 替换其原字段里的原名",
                        "ja": "例：「ウォッシュロトム」→ 元のフィールドの名前を置き換え",
                        "en": "e.g. answer 'Rotom-Wash' -> replaces the original entry in its field"},
        },
        "options": [
            {"id": "drop", "label": {"zh": "都不是，删掉它", "ja": "どれでもない。削除で",
                                     "en": "None of those — drop it"},
             "maps_to": {"target": "context", "remove": {"locked": "{query}"}}},
        ],
        "triggers_on": ["conflict:ambiguous_name"], **_CONFLICT_COMMON,
    },
    {
        "id": "conflict_unresolved_name",
        "text": {"zh": "没认出“{query}”——换个写法试试？（中文/英文/日文、别名都行）",
                 "ja": "「{query}」を特定できませんでした——別の表記で教えてください（日中英どれでも）。",
                 "en": "Couldn't resolve '{query}' — try another spelling? (zh/en/ja, aliases fine)"},
        "free_form": {
            "resolvers": ["dex:pokemon", "dex:item"],
            "maps_to": {"target": "context",
                        "fields": ["locked", "prefer", "owned", "avoid", "avoid_soft"]},
            "example": {"zh": "例：“烈咬陆鲨”/“Garchomp”/“ガブリアス”均可",
                        "ja": "例：「ガブリアス」/「Garchomp」など",
                        "en": "e.g. 'Garchomp' / '烈咬陆鲨' / 'ガブリアス' all resolve"},
        },
        "options": [
            {"id": "drop", "label": {"zh": "删掉这个名字", "ja": "その名前は削除", "en": "Drop that name"},
             "maps_to": {"target": "context", "remove": {"locked": "{query}"}}},
        ],
        "triggers_on": ["conflict:unresolved_name"], **_CONFLICT_COMMON,
    },
]

CATALOG += CONFLICT_QUESTIONS


# --------------------------------------------------------------------------- the new-build guided walk
# The onboarding flow the skill was ORIGINALLY built for (design §19.6, two-mode intake): an open-ended
# build request (the user wants a team but has not self-supplied the deep intent — purpose / anchor /
# style / floor) completes this base-question SET once, purpose-first — fired PER BUILD TASK (a second
# distinct build in the same session runs its own walk), distinct from the STEADY-STATE <=3-per-round
# gap refinement that serves expert pastes / narrow asks / post-walk residuals. It is driven by the
# base set (NOT by context-audit gaps): two base questions (availability_and_avoid, answer_shape)
# carry no gap trigger, so a gap-driven flow silently drops them — exactly the drift this restores.
# Each entry lists the context keys whose presence means the request already answers that dimension
# (skip it in the walk); the LIST ORDER is the purpose-first ask order (no per-question order field —
# see the module docstring / test_no_flow_policy).
ONBOARDING_WALK: list[tuple[str, list[str]]] = [
    ("format",                   ["format"]),
    ("use_case",                 ["meta_conformance", "locked", "prefer", "keep_mega", "wants"]),
    ("anchor_pokemon_or_mega",   ["locked", "prefer", "keep_mega"]),
    ("anchor_theme_or_gameplan", ["wants"]),
    ("starting_point",           ["owned", "owned_only"]),
    ("availability_and_avoid",   ["avoid", "avoid_soft", "exclude_tactics"]),
    ("play_posture",             ["style_lean"]),
    ("answer_shape",             []),
    ("extra_requirements",       ["benchmarks"]),
]
_ONBOARDING_IDS = [i for i, _ in ONBOARDING_WALK]
ONBOARDING_IDS = tuple(_ONBOARDING_IDS)

# Cognitive-load-affinity groups: `intake --next` (next_batch) drives the walk ONE group at a time —
# 1-3 related questions per step, varying by load (design §19.6), NOT one question at a time (too slow)
# and NOT the whole walk at once (overwhelming). Related light questions group; the typing-heavy ones
# (answer_mode text) sit with lighter neighbours so no batch is all-typing. The concatenation MUST
# equal ONBOARDING_WALK's order (asserted) — the groups are just batch boundaries on the same walk.
ONBOARDING_GROUPS: list[tuple[str, list[str]]] = [
    ("framing",      ["format", "use_case"]),                                  # what & why (2 light)
    ("build_around", ["anchor_pokemon_or_mega", "anchor_theme_or_gameplan"]),  # any pick / any plan
    ("constraints",  ["starting_point", "availability_and_avoid"]),            # pool / avoid
    ("finishing",    ["play_posture", "answer_shape", "extra_requirements"]),  # style / shape / targets
]

# How each base question is answered — the renderer hint the agent uses (choice = pick from options;
# choice_or_text = pick a preset OR type; text = free-form primary, options are just a skip/none). Also
# what makes a question "heavy" (text) vs "light" (choice) for the batch feel.
_ANSWER_MODE: dict[str, str] = {
    "format": "choice", "use_case": "choice",
    "anchor_pokemon_or_mega": "text", "anchor_theme_or_gameplan": "text",
    "starting_point": "choice_or_text", "availability_and_avoid": "choice_or_text",
    "play_posture": "choice", "answer_shape": "choice", "extra_requirements": "text",
}


def _stamp_tiers() -> None:
    """Stamp tier / answered_when / answer_mode onto the catalog from the SINGLE sources
    (ONBOARDING_WALK + _ANSWER_MODE); the asserts guard an id typo from silently dropping a base
    question out of the walk or the batch groups. No `walk_order` field is stamped — the ask order
    lives only in the ONBOARDING_WALK / ONBOARDING_GROUPS lists (test_no_flow_policy bans an `order`
    key so menu mode stays a flat menu)."""
    by_id = {q["id"]: q for q in CATALOG}
    unknown = set(_ONBOARDING_IDS) - set(by_id)
    assert not unknown, f"ONBOARDING_WALK references unknown catalog ids: {sorted(unknown)}"
    grouped = [qid for _, ids in ONBOARDING_GROUPS for qid in ids]
    assert grouped == _ONBOARDING_IDS, "ONBOARDING_GROUPS must partition ONBOARDING_WALK in walk order"
    assert set(_ANSWER_MODE) == set(_ONBOARDING_IDS), "_ANSWER_MODE must cover exactly the base questions"
    for qid, answered in ONBOARDING_WALK:
        q = by_id[qid]
        q["tier"] = "onboarding"
        q["answered_when"] = list(answered)
        q["answer_mode"] = _ANSWER_MODE[qid]
    for q in CATALOG:
        q.setdefault("tier", "conflict")     # everything not in the walk is a conflict template


_stamp_tiers()
_CATALOG_BY_ID = {q["id"]: q for q in CATALOG}


def _truthy(v: Any) -> bool:
    """A context field is 'answered' when present with a non-empty value (owned_only=True counts; an
    empty list/str/dict does not)."""
    if isinstance(v, (list, dict, str)):
        return len(v) > 0
    return bool(v)


def covered_dimensions(context: dict[str, Any] | None) -> dict[str, list[str]]:
    """Which onboarding dimensions the build-context already answers -> {question_id: [keys hit]}.
    Purely presence-based over top-level context keys — never parses free text."""
    ctx = context if isinstance(context, dict) else {}
    covered: dict[str, list[str]] = {}
    for qid, keys in ONBOARDING_WALK:
        hit = [k for k in keys if _truthy(ctx.get(k))]
        if hit:
            covered[qid] = hit
    return covered


BEGINNER_DEFAULTS: dict[str, Any] = {
    "format": {"value": None, "source": "library_coverage",
               "note": "NO safe default — a missing format is BLOCKING; double only on the user's "
                       "explicit delegation (mainstream environment + richest library), disclosed."},
    "meta_conformance": {"value": "proven", "source": "heuristic"},
    "style_lean": {"value": None, "source": "heuristic",
                   "note": "no posture lens — read structural facts directly, disclose."},
    "candidate_count": {"value": "2-3", "source": "protocol"},
    "benchmarks": {"value": None, "source": "protocol",
                   "note": "no hard benchmark by default; validate + full matchup backstop."},
    "season_rule": {"value": "current environment stamp", "source": "environment"},
}

RESOLVER_VOCABULARY: dict[str, str] = {
    "dex:pokemon": "any language / alias / typo -> canonical species (dex resolve bridge; "
                   "ambiguous names never auto-pick — echo back)",
    "dex:item": "item names -> canonical items (strict; an item never masquerades as a species)",
    "dex:move": "move names -> canonical moves (for benchmark specs)",
    "team:parse": "a pasted team in ANY format -> team-json (the AI transcribes; `team.py parse` "
                  "normalizes — the skill never reads free text)",
    "tactic_classifier": "plain-language play-style phrases -> exclude_tactics/wants tokens; the "
                         "mapping table IS this catalog's availability_and_avoid options — the "
                         "token vocabulary never appears in user-facing wording",
    "benchmark_parser": "spoken hard requirements -> structured benchmarks[] (schema §7); each "
                        "parsed entry echoes back for confirmation",
}


_COMMON_NOTES = [
    "every maps_to lands on a typed build-context field/enum (or a declared draft field); use_case "
    "presets are overridable by later answers.",
    "de-jargoned by construction: tactic tokens live in maps_to only; user-facing wording stays "
    "plain language with examples.",
]

_MENU_NOTES = [
    "MODE = menu (STEADY-STATE gap refinement): use this when a build-context already exists — an "
    "expert paste, a narrow ask, or a post-onboarding refinement round. Pick <=3 questions PER "
    "ROUND for the context-audit gaps you choose to ask, THEN re-audit and iterate; <=3 is a "
    "per-round BATCH SIZE, never a cap on total questions. Which to ask = the audit's gaps "
    "(triggers_on keys into the P2 gap vocabulary) + your judgment of asking-cost vs info_value; "
    "safe_default gaps are applied + DISCLOSED, not force-asked.",
    "for a NEW open-ended build whose intent is not yet elicited (fires per build TASK, not per "
    "session — a later distinct build gets its own walk), call intake with onboarding=True (CLI: "
    "`--onboarding [--context ctx.json]`) instead: it returns guided_walk = the base question set to "
    "COMPLETE once, minus what the context already answers. That walk is the onboarding flow — ask "
    "all of it, not a <=3 subset.",
    "free-form answers are resolved via the listed resolvers and ambiguities are ECHOED BACK "
    "(must_confirm), never auto-picked.",
    *_COMMON_NOTES,
]

_ONBOARDING_NOTES = [
    "MODE = onboarding (the new-build guided walk OVERVIEW): guided_walk lists the base questions to "
    "COMPLETE in this pass, in purpose-first order, minus the dimensions the context already answers "
    "(already_answered). This is the full plan — the restored onboarding flow, NOT the <=3-per-round "
    "gap refinement; you must complete the whole set, not a <=3 subset.",
    "this overview does NOT itself pace — it returns the whole plan. To ACTUALLY ask it, DRIVE it with "
    "`intake --next --context <ctx>` — that returns the next batch of 1-3 related questions (a numbered "
    "menu), one step at a time, and is where pacing is enforced; do NOT render this guided_walk as a "
    "wall of questions. Loop the stepper until done. After the walk, translate answers into a "
    "build-context and run context-audit; residual conflicts/gaps then use the steady-state menu.",
    *_COMMON_NOTES,
]

_STEP_NOTES = [
    "MODE = onboarding_step (the batch driver — the way to actually ASK the walk): `batch` is the next "
    "1-3 related questions. Ask THIS batch together, then STOP. Render each question's options as a "
    "NUMBERED menu (universal — the user replies by number; options carry `n` + trilingual labels + "
    "maps_to) or the host's clickable question UI where it exists (e.g. Claude Code AskUserQuestion — "
    "a progressive enhancement, never a requirement). answer_mode: choice = pick a number; "
    "choice_or_text = pick OR type; text = type a free answer (every question also offers a skip/none "
    "option). Ambiguous free-form (names/benchmarks) ECHOES BACK before committing.",
    "then apply each answer's maps_to to the build-context, ADD this batch's question ids to `answered`, "
    "and call `intake --next --context <updated> --answered <all resolved ids so far>` again for the "
    "NEXT batch; loop until done:true. `answered` is REQUIRED (not optional bookkeeping) and holds "
    "EVERY resolved dimension — both the ids you have asked, AND any the INITIAL request already "
    "resolved via the draft/answer-shape that --context cannot show (e.g. answer_shape when the user "
    "opened with 'just one team'); pre-seed those so they are not re-asked. Unknown ids -> rc=2 (a "
    "dropped typo would re-ask and spin the loop). Pacing is enforced by THIS `--next` stepper (it only "
    "ever returns the next group); `--onboarding` is only the overview and does not itself pace. Batch "
    "size varies 1-3 by cognitive load (light choice questions group up to 3; typing-heavy come with "
    "fewer). remaining_after = questions still pending AFTER this batch.",
    *_COMMON_NOTES,
]

_STEP_DONE_NOTES = [
    "done:true — every base question is answered or pruned by the context. Assemble the build-context "
    "from the collected answers and proceed to context-audit -> grounding -> slate (design §19.1).",
    *_COMMON_NOTES,
]


def intake_catalog(game_format: str | None = None, *, context: dict[str, Any] | None = None,
                   onboarding: bool = False) -> dict[str, Any]:
    """The static catalog; `game_format` narrows format-tagged options (singles has no Tailwind
    tempo option, doubles no entry-hazards option). Pure data — no policy, no flow. Two modes
    (design §19.6): menu (default) is the full catalog for steady-state <=3-per-round gap
    refinement; onboarding=True ALSO emits `guided_walk` = the base question set to complete once
    (purpose-first order), minus the dimensions `context` already answers (`already_answered`) — the
    restored onboarding flow for open-ended builds (fired per build task)."""
    questions = []
    for q in CATALOG:
        q = copy.deepcopy(q)      # the module-level CATALOG must never leak by reference — a
        #                           consumer mutating the output must not poison later calls
        if game_format and q.get("options"):
            q["options"] = [o for o in q["options"]
                            if not o.get("formats") or game_format in o["formats"]]
        questions.append(q)
    out: dict[str, Any] = {
        "kind": "intake",
        "consumer": "ai-orchestration",
        "mode": "onboarding" if onboarding else "menu",
        "game_format": game_format,
        "questions": questions,
        "beginner_defaults": BEGINNER_DEFAULTS,
        "resolver_vocabulary": RESOLVER_VOCABULARY,
        "notes": list(_ONBOARDING_NOTES if onboarding else _MENU_NOTES),
    }
    if onboarding:
        covered = covered_dimensions(context)
        by_id = {q["id"]: q for q in questions}
        # guided_walk carries the same (format-filtered) question objects, in ONBOARDING_WALK order,
        # minus the covered dimensions — the ordered, ready-to-ask base set.
        out["guided_walk"] = [by_id[qid] for qid in _ONBOARDING_IDS if qid not in covered]
        out["already_answered"] = [{"id": qid, "covered_by": covered[qid]}
                                   for qid in _ONBOARDING_IDS if qid in covered]
    return out


def _numbered_question(qid: str, game_format: str | None) -> dict[str, Any]:
    """A deep-copied catalog question, options format-filtered and 1-numbered (`n`) so the agent can
    render a numbered menu with zero extra work."""
    q = copy.deepcopy(_CATALOG_BY_ID[qid])
    if game_format and q.get("options"):
        q["options"] = [o for o in q["options"] if not o.get("formats") or game_format in o["formats"]]
    for i, o in enumerate(q.get("options") or [], start=1):
        o["n"] = i
    return q


def next_batch(context: dict[str, Any] | None = None, game_format: str | None = None,
               answered: list[str] | None = None) -> dict[str, Any]:
    """Drive the new-build guided walk ONE batch (1-3 related questions) at a time (design §19.6).

    Returns the next ONBOARDING_GROUPS group's still-pending questions (numbered for a menu). Pacing
    is enforced HERE — only ever the next group is returned. Loop: show the batch, apply each answer's
    maps_to to the build-context, add the batch's ids to `answered`, and call again, until done:true.

    A question is PENDING unless it is RESOLVED — either auto-detected from the context
    (`covered_dimensions`) or listed in `answered`. `answered` is REQUIRED for termination, not
    optional bookkeeping, and covers the TWO things the context cannot show: (1) the questions you have
    already asked THIS walk; (2) dimensions the INITIAL request resolved via the DRAFT / answer-shape
    rather than the context (answer_shape when the user said 'just one team' -> single_team_requested;
    use_case 'around a favourite' -> request_expressive; 'learn' -> answer-shape mode) — PRE-SEED
    `answered` with those instead of re-asking them. Progress = covered ∪ answered, i.e. what is
    RESOLVED, not what happened to populate a context field. Ids not in the base set are echoed in
    `unknown_answered` (the CLI turns a non-empty list into rc=2 — a silently dropped typo would
    re-ask a resolved dimension and spin the loop). Batch size varies 1-3."""
    from canonhash import content_hash
    covered = covered_dimensions(context)
    answered_ids = [qid for qid in (answered or []) if qid in _ONBOARDING_IDS]
    unknown = [qid for qid in (answered or []) if qid not in _ONBOARDING_IDS]
    done_ids = set(covered) | set(answered_ids)
    total_pending = sum(1 for qid in _ONBOARDING_IDS if qid not in done_ids)
    out: dict[str, Any] = {
        "kind": "intake",
        "mode": "onboarding_step",
        "context_fingerprint": content_hash(context or {}, 24),
        "consumer": "ai-orchestration",
        "game_format": game_format,
        "already_answered": [{"id": qid, "covered_by": covered[qid]}
                             for qid in _ONBOARDING_IDS if qid in covered],
        "answered": [qid for qid in _ONBOARDING_IDS if qid in set(answered_ids)],
        "unknown_answered": unknown,
    }
    for gname, ids in ONBOARDING_GROUPS:
        pending = [qid for qid in ids if qid not in done_ids]
        if pending:
            out.update({
                "done": False,
                "group": gname,
                "batch": [_numbered_question(qid, game_format) for qid in pending],
                "remaining_after": total_pending - len(pending),
                "notes": list(_STEP_NOTES),
            })
            return out
    out.update({"done": True, "group": None, "batch": [], "remaining_after": 0,
                "notes": list(_STEP_DONE_NOTES)})
    return out
