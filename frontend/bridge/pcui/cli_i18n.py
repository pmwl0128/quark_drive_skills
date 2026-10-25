"""Human-facing text for the pcui CLI. Wire JSON and command names remain invariant."""
from __future__ import annotations

import os

LANGS = ("zh", "ja", "en")
_lang = "en"


def set_lang(value: str | None = None) -> str:
    global _lang
    candidate = (value or os.environ.get("POKEMON_CHAMPIONS_LANG") or "en").lower()
    _lang = candidate if candidate in LANGS else "en"
    return _lang


def t(key: str, **values: object) -> str:
    row = TEXT.get(key, {})
    text = row.get(_lang) or row.get("en") or key
    return text.format(**values) if values else text


_ARGPARSE = {
    "zh": {
        "usage: ": "用法：",
        "positional arguments": "位置参数",
        "options": "选项",
        "show this help message and exit": "显示此帮助信息并退出",
        "the following arguments are required: %s": "缺少必需参数：%s",
        "unrecognized arguments: %s": "无法识别的参数：%s",
        "argument %s: expected one argument": "参数 %s 需要一个值",
        "argument %s: invalid choice: %(value)r (choose from %(choices)s)":
            "参数 %s：%(value)r 不是有效选项（可选：%(choices)s）",
    },
    "ja": {
        "usage: ": "使用方法：",
        "positional arguments": "位置引数",
        "options": "オプション",
        "show this help message and exit": "このヘルプを表示して終了",
        "the following arguments are required: %s": "次の引数が必要です：%s",
        "unrecognized arguments: %s": "認識できない引数：%s",
        "argument %s: expected one argument": "引数 %s には値が必要です",
        "argument %s: invalid choice: %(value)r (choose from %(choices)s)":
            "引数 %s：%(value)r は選べません（選択肢：%(choices)s）",
    },
}


def argparse_text(text: str) -> str:
    """Translate argparse's fixed UI while leaving flags and choices untouched."""
    return _ARGPARSE.get(_lang, {}).get(text, text)


TEXT = {
    "description": {"zh": "Pokémon Champions 可视化界面与本地桥接命令行工具。", "ja": "Pokémon Champions の画面とローカルブリッジ用CLI。", "en": "Pokémon Champions UI and local bridge CLI."},
    "lang_help": {"zh": "显示文本的语言；JSON 与命令名保持不变", "ja": "表示テキストの言語。JSONとコマンド名は変わりません", "en": "language for display text; JSON and command names stay unchanged"},
    "serve_help": {"zh": "启动本地桥接服务", "ja": "ローカルブリッジを起動", "en": "run the local bridge"},
    "online_help": {"zh": "启动公开在线 API", "ja": "公開オンラインAPIを起動", "en": "run the public online API"},
    "doctor_help": {"zh": "检查运行环境", "ja": "実行環境を確認", "en": "check the environment"},
    "session_help": {"zh": "管理会话", "ja": "セッションを管理", "en": "manage sessions"},
    "artifact_help": {"zh": "管理会话产物", "ja": "セッション成果物を管理", "en": "manage session artifacts"},
    "run_help": {"zh": "在已启动的服务中运行一个允许的队伍算子", "ja": "起動中のサービスで許可済み構築処理を1つ実行", "en": "run one whitelisted team operator in the daemon"},
    "dist_help": {"zh": "挂载到根路径的已构建网页", "ja": "ルートに配信するビルド済みWeb画面", "en": "built SPA to host at /"},
    "projection_help": {"zh": "挂载到 /projection 的静态数据投影", "ja": "/projection に配信する静的データ", "en": "static projection to host at /projection"},
    "provider_help": {"zh": "opencode 使用 OpenCode Go 订阅的 DeepSeek Flash，不可用时回退 DeepSeek 官方 API；deepseek 只用官方 API；none 禁用模型；echo 仅供开发", "ja": "opencode は OpenCode Go サブスクリプションの DeepSeek Flash を使用し、利用不可時は DeepSeek 公式 API にフォールバックします。deepseek は公式 API のみ、none はモデルを無効化、echo は開発専用です", "en": "opencode uses DeepSeek Flash on the OpenCode Go subscription with the official DeepSeek API as fallback; deepseek uses only the official API; none disables the model; echo is for development"},
    "env_file_help": {"zh": "持久环境变量文件（KEY=value；已设置的进程变量优先）", "ja": "永続環境変数ファイル（KEY=value。設定済みのプロセス変数を優先）", "en": "persistent environment file (KEY=value; existing process variables take precedence)"},
    "qa_limit_help": {"zh": "每个匿名身份每日（UTC）可提问次数", "ja": "匿名IDごとのUTC日次質問上限", "en": "questions per anonymous identity per UTC day"},
    "qa_budget_help": {"zh": "每日模型 token 总预算", "ja": "モデルのUTC日次トークン予算", "en": "total model tokens per UTC day"},
    "idle_help": {"zh": "查询进程空闲多少秒后回收；0 表示不回收", "ja": "照会ワーカーを回収するまでのアイドル秒数。0は回収なし", "en": "idle seconds before query workers are reclaimed; 0 means never"},
    "op_help": {"zh": "队伍算子名称（服务端执行允许列表）", "ja": "構築処理名（サーバー側の許可リストを適用）", "en": "team operator name (the daemon enforces its allowlist)"},
    "args_help": {"zh": "JSON 对象形式的额外算子字段", "ja": "JSONオブジェクト形式の追加フィールド", "en": "extra operator fields as a JSON object"},
    "file_help": {"zh": "读入 JSON 文件并写入算子的 {field} 字段", "ja": "JSONファイルを読み込み、処理の {field} フィールドへ設定", "en": "inline a JSON file into the operator's {field} field"},
    "dir_help": {"zh": "明确指定技能目录（优先于 --target）", "ja": "スキルディレクトリを直接指定（--targetより優先）", "en": "explicit skills directory (overrides --target)"},
    "yes_help": {"zh": "跳过确认", "ja": "確認を省略", "en": "skip confirmation"},
    "install_help": {"zh": "安装 AI 助手配套技能", "ja": "AIアシスタント用コンパニオンスキルをインストール", "en": "install the companion skill for an AI assistant"},
    "uninstall_help": {"zh": "卸载 AI 助手配套技能", "ja": "AIアシスタント用コンパニオンスキルを削除", "en": "uninstall the companion skill for an AI assistant"},
    "companion_dest": {"zh": "配套技能 → {dest}", "ja": "コンパニオンスキル → {dest}", "en": "companion skill → {dest}"},
    "expose_local": {"zh": "警告：绑定 {host} 会使本地桥接服务可被本机以外的设备访问。", "ja": "警告：{host} にバインドすると、ローカルブリッジが他の端末からもアクセス可能になります。", "en": "WARNING: binding {host} exposes the bridge beyond this machine."},
    "expose_online": {"zh": "警告：绑定 {host} 会使在线 API 可被本机以外的设备访问。", "ja": "警告：{host} にバインドすると、オンラインAPIが他の端末からもアクセス可能になります。", "en": "WARNING: binding {host} exposes the online API beyond this machine."},
    "confirm": {"zh": "输入 yes 继续：", "ja": "続行するには yes と入力：", "en": "type 'yes' to continue: "},
    "daemon_running": {"zh": "pcui 服务已在运行（PID {pid}，端口 {port}）；请先停止它。", "ja": "pcuiサービスはすでに実行中です（PID {pid}、ポート {port}）。先に停止してください。", "en": "a pcui daemon is already running (pid {pid}, port {port}); stop it first."},
    "daemon_unreachable": {"zh": "daemon.json 记录的进程 {pid} 仍在运行（端口 {port}），但服务无响应。为避免并发写入，已拒绝直接写 SQLite；请停止服务或修复连接。", "ja": "daemon.json上のプロセス {pid}（ポート {port}）は生存していますが応答しません。二重書き込みを避けるためSQLiteへの直接書き込みを拒否しました。サービスを停止するか接続を修復してください。", "en": "daemon.json says pid {pid} is alive on port {port} but is not answering; direct SQLite writes were refused to prevent split-brain. Stop the daemon or fix connectivity."},
    "llm_key_missing": {"zh": "警告：所选 provider 未配置可用密钥；设置 PCUI_OC_API_KEY、PCUI_DEEPSEEK_API_KEY 或 PCUI_LLM_API_KEY 后再启用模型接口。", "ja": "警告：選択した provider の利用可能なキーがありません。PCUI_OC_API_KEY、PCUI_DEEPSEEK_API_KEY、または PCUI_LLM_API_KEY を設定してください。", "en": "WARNING: no usable key is configured for the selected provider; set PCUI_OC_API_KEY, PCUI_DEEPSEEK_API_KEY or PCUI_LLM_API_KEY to enable the model API."},
    "unlimited_refused": {"zh": "拒绝启用本地无限额模式：该模式只允许回环地址且必须未设置 PCUI_PUBLIC_ORIGIN。", "ja": "ローカル無制限モードを拒否しました。このモードはループバックで PCUI_PUBLIC_ORIGIN 未設定の場合のみ使用できます。", "en": "refusing local unmetered mode: it requires a loopback bind and no PCUI_PUBLIC_ORIGIN"},
    "env_file_invalid": {"zh": "环境变量文件格式错误：{path} 第 {line} 行", "ja": "環境変数ファイルの形式が不正です：{path} の {line} 行目", "en": "invalid environment file syntax: {path}, line {line}"},
    "secret_missing": {"zh": "警告：未设置 PCUI_ONLINE_SECRET，将使用仅本次进程有效的密钥；设备 cookie 和 IP 哈希会在重启后失效，仅适合开发环境。", "ja": "警告：PCUI_ONLINE_SECRET が未設定のため、プロセス限りの鍵を使用します。再起動すると端末CookieとIPハッシュが変わります。開発用途に限ってください。", "en": "WARNING: PCUI_ONLINE_SECRET is not set; using a per-process secret. Device cookies and IP hashes reset on restart, so this is for development only."},
    "doctor_ok": {"zh": "环境检查：全部正常。", "ja": "環境チェック：問題ありません。", "en": "doctor: all good."},
    "doctor_fail": {"zh": "环境检查：发现问题。", "ja": "環境チェック：問題が見つかりました。", "en": "doctor: problems found."},
    "status_ok": {"zh": "正常", "ja": "正常", "en": "ok"},
    "status_fail": {"zh": "异常", "ja": "エラー", "en": "FAIL"},
    "check_skill": {"zh": "技能命令：{name}", "ja": "スキルCLI：{name}", "en": "skill CLI: {name}"},
    "check_mirror": {"zh": "技能镜像目录", "ja": "スキルのミラーディレクトリ", "en": "mirror tree present"},
    "check_quickjs": {"zh": "quickjs-ng 运行时", "ja": "quickjs-ngランタイム", "en": "quickjs-ng runtime"},
    "check_node": {"zh": "Node.js 回退", "ja": "Node.jsフォールバック", "en": "Node.js fallback"},
    "check_meta": {"zh": "当前环境索引", "ja": "現在の環境インデックス", "en": "meta current.json"},
    "check_write": {"zh": "数据目录可写", "ja": "データディレクトリへ書き込み可能", "en": "data directory writable"},
    "check_daemon": {"zh": "本地服务", "ja": "ローカルサービス", "en": "daemon"},
    "check_probe": {"zh": "图鉴端到端测试", "ja": "図鑑のエンドツーエンドテスト", "en": "dex end-to-end probe"},
    "running": {"zh": "运行于端口 {port}", "ja": "ポート{port}で実行中", "en": "running on port {port}"},
    "not_running": {"zh": "未运行", "ja": "停止中", "en": "not running"},
    "calc_runtime": {"zh": "进程内计算引擎", "ja": "プロセス内計算エンジン", "en": "in-process calculator engine"},
    "calc_missing": {"zh": "未找到 quickjs-ng 或 Node.js，计算器无法运行", "ja": "quickjs-ngもNode.jsも見つからないため、計算機を実行できません", "en": "neither quickjs-ng nor Node.js was found; the calculator will fail"},
    "need_session": {"zh": "未指定会话：传入会话 id（--session / --id），或先运行 `pcui session use <id>`，或设置 PCUI_SESSION。", "ja": "セッションが指定されていません。セッションID（--session / --id）を渡すか、先に `pcui session use <id>` を実行するか、PCUI_SESSION を設定してください。", "en": "no session given: pass a session id (--session / --id), run `pcui session use <id>` first, or set PCUI_SESSION."},
    "session_not_found": {"zh": "找不到会话 {id}", "ja": "セッション {id} が見つかりません", "en": "session {id} not found"},
    "session_current": {"zh": "当前会话：{id}", "ja": "現在のセッション：{id}", "en": "current session: {id}"},
    "session_none": {"zh": "尚未选择当前会话。", "ja": "現在のセッションは未選択です。", "en": "no current session."},
    "session_id_help": {"zh": "会话 id（show / use；同 --id）", "ja": "セッションID（show / use。--id と同じ）", "en": "session id (show / use; same as --id)"},
    "intent_help": {"zh": "create：自然语言需求", "ja": "create：自然言語の依頼内容", "en": "create: the request in plain language"},
    "kind_help": {"zh": "create：任务类型", "ja": "create：タスクの種類", "en": "create: task kind"},
    "team_help": {"zh": "create：附带的队伍 team-json 文件", "ja": "create：添付するチームの team-json ファイル", "en": "create: team-json file to attach"},
    "team_name_help": {"zh": "create：附带队伍的显示名称", "ja": "create：添付チームの表示名", "en": "create: display name of the attached team"},
    "team_invalid": {"zh": "{path} 不是包含 pokemon 列表的 team-json。", "ja": "{path} は pokemon リストを含む team-json ではありません。", "en": "{path} is not a team-json with a pokemon list."},
    "team_format_conflict": {"zh": "队伍文件的赛制是 {team}，与 --format {given} 不一致。", "ja": "チームファイルの形式は {team} で、--format {given} と一致しません。", "en": "the team file is {team}, which contradicts --format {given}."},
    "artifact_session_help": {"zh": "会话 id（省略时使用 PCUI_SESSION 或 `pcui session use` 选定的会话）", "ja": "セッションID（省略時は PCUI_SESSION か `pcui session use` で選んだセッション）", "en": "session id (defaults to PCUI_SESSION or the one chosen with `pcui session use`)"},
    "artifact_revision_help": {"zh": "put 必填：开始工作时从 session show 读到的 revision", "ja": "put に必須：作業開始時に session show で読んだ revision", "en": "required for put: revision read with session show before starting the work"},
    "artifact_revision_required": {"zh": "artifact put 必须传入 --revision <非负整数>，使用开始工作时从 session show 读到的版本；冲突后重读并确认内容。", "ja": "artifact put には --revision <非負整数> が必要です。作業開始時の session show の版を使い、競合後は再読して内容を確認してください。", "en": "artifact put requires --revision <non-negative integer> from session show before starting the work; on conflict, re-read and review the content."},
    "artifact_conflict": {"zh": "工件未写入：预期 revision {expected}，当前为 {actual}。请重读会话并确认内容，不要直接换版本号重试。", "ja": "成果物は未保存です：期待する revision は {expected}、現在は {actual}。セッションを再読して内容を確認し、版だけを変えて再試行しないでください。", "en": "artifact not written: expected revision {expected}, current {actual}. Re-read the session and review the content; do not retry by only changing the revision."},
    "run_session_help": {"zh": "把门控算子（context-audit、frame、slate-evaluate、checkpoint、answer-audit、intake --next、tune、diagnose）的产物直接写入会话；不带值时用当前会话", "ja": "ゲート処理（context-audit、frame、slate-evaluate、checkpoint、answer-audit、intake --next、tune、diagnose）の成果物をセッションへ直接書き込む。値なしなら現在のセッション", "en": "submit gate operators' artifacts (context-audit, frame, slate-evaluate, checkpoint, answer-audit, intake --next, tune, diagnose) to the session; without a value, the current session"},
    "wait_for_help": {"zh": "wait：等待的面板产物种类，逗号分隔", "ja": "wait：待つパネル成果物の種類（カンマ区切り）", "en": "wait: panel artifact kinds to wait for, comma-separated"},
    "wait_timeout_help": {"zh": "wait：最长等待秒数", "ja": "wait：最大待機秒数", "en": "wait: give up after this many seconds"},
    "wait_need_daemon": {"zh": "pcui session wait 需要已启动的本地服务（先运行 `pcui serve`）。", "ja": "pcui session wait には起動中のローカルサービスが必要です（先に `pcui serve` を実行）。", "en": "pcui session wait needs a running daemon (`pcui serve`)."},
    "wait_started": {"zh": "正在等待会话 {id} 中面板的 {kinds}……", "ja": "セッション {id} でパネルの {kinds} を待っています…", "en": "waiting for the panel's {kinds} on session {id}…"},
    "wait_ready": {"zh": "已收到面板的 {kind}。", "ja": "パネルの {kind} を受け取りました。", "en": "received the panel's {kind}."},
    "wait_timeout": {"zh": "等待超时：会话 {id} 中用户还没有在面板作答。请提示用户在面板决定后再让你继续。", "ja": "待機がタイムアウトしました：セッション {id} でまだパネルの回答がありません。パネルで決めてから続行を依頼するよう伝えてください。", "en": "timed out: the reader has not answered in the panel for session {id}. Ask them to answer there, then continue."},
    "out_help": {"zh": "把完整结果写入文件，终端只打印摘要", "ja": "完全な結果をファイルに書き、端末には要約だけを表示", "en": "write the full result to a file and print only a summary"},
    "mcp_help": {"zh": "以 stdio MCP 服务器提供会话、产物与算子工具（需已启动的本地服务）", "ja": "セッション・成果物・処理のツールを stdio MCP サーバーとして提供（起動中のローカルサービスが必要）", "en": "serve session, artifact and operator tools as a stdio MCP server (needs a running daemon)"},
    "mcp_config_help": {"zh": "打印在 Claude Code 与 Codex 中注册的配置", "ja": "Claude Code と Codex への登録設定を表示", "en": "print how to register it with Claude Code and Codex"},
    "open_help": {"zh": "生成新的一次性登录地址并打开本地界面", "ja": "新しいワンタイムURLを発行してローカル画面を開く", "en": "mint a fresh one-time sign-in link and open the local UI"},
    "open_session_help": {"zh": "直接打开这个会话", "ja": "このセッションを直接開く", "en": "open this session directly"},
    "no_browser_help": {"zh": "只打印地址，不打开浏览器", "ja": "URLを表示するだけでブラウザは開かない", "en": "print the link without opening a browser"},
    "open_need_daemon": {"zh": "pcui open 需要已启动的本地服务（先运行 `pcui serve`）。", "ja": "pcui open には起動中のローカルサービスが必要です（先に `pcui serve` を実行）。", "en": "pcui open needs a running daemon (`pcui serve`)."},
    "workspace_help": {"zh": "用户工作区目录（默认：pc.env 中记住的 PCUI_WORKSPACE_DIR，否则为 ./storage）", "ja": "ユーザーワークスペース（既定：pc.env に記録された PCUI_WORKSPACE_DIR、なければ ./storage）", "en": "user workspace folder (default: PCUI_WORKSPACE_DIR remembered in pc.env, else ./storage)"},
    "workspace_missing": {"zh": "记住的工作区 {path} 不存在。为避免打开空资料库，已停止启动。若资料已移动，请用 --workspace 指向新位置；若要在原处新建，请运行 `pcui serve --workspace \"{path}\"`。", "ja": "記録されたワークスペース {path} が存在しません。空のライブラリを開かないよう起動を中止しました。移動した場合は --workspace で新しい場所を指定し、同じ場所に新規作成する場合は `pcui serve --workspace \"{path}\"` を実行してください。", "en": "the remembered workspace {path} does not exist, so the bridge did not start rather than open an empty library. If it moved, pass --workspace with the new location; to create it again there, run `pcui serve --workspace \"{path}\"`."},
    "check_companion": {"zh": "AI 助手配套技能（{target}）", "ja": "AIアシスタント用コンパニオン（{target}）", "en": "assistant companion skill ({target})"},
    "companion_installed": {"zh": "已安装", "ja": "導入済み", "en": "installed"},
    "companion_outdated": {"zh": "需更新", "ja": "要更新", "en": "outdated"},
    "companion_missing": {"zh": "未安装", "ja": "未導入", "en": "missing"},
    "companion_hint": {"zh": "提示：让 AI 助手处理网页会话前，运行 `{cmd} install-agent`（Codex 加 --target codex）。", "ja": "ヒント：AIアシスタントにWebセッションを処理させる前に `{cmd} install-agent` を実行してください（Codex は --target codex）。", "en": "tip: run `{cmd} install-agent` (add --target codex for Codex) before an assistant works on Console sessions."},
    "no_artifact": {"zh": "会话中没有“{kind}”产物", "ja": "セッションに「{kind}」成果物がありません", "en": "session has no '{kind}' artifact"},
    "run_need_daemon": {"zh": "pcui run 需要已启动的服务（先运行 `pcui serve`）；算子会在常驻查询进程中执行。", "ja": "pcui run には起動中のサービスが必要です（先に `pcui serve` を実行）。処理は常駐ワーカー内で実行されます。", "en": "pcui run needs a running daemon (`pcui serve`); operators execute inside its resident workers."},
    "args_object": {"zh": "--args 必须是 JSON 对象", "ja": "--args はJSONオブジェクトで指定してください", "en": "--args must be a JSON object"},
    "write_confirm": {"zh": "输入 yes 写入：", "ja": "書き込むには yes と入力：", "en": "type 'yes' to write: "},
    "installed": {"zh": "已安装。触发语：“处理当前 Pokémon UI 会话”／“work on the current Pokémon UI session”。", "ja": "インストールしました。呼び出し文：「現在の Pokémon UI セッションを処理して」／「work on the current Pokémon UI session」。", "en": "Installed. Trigger phrase: \"work on the current Pokémon UI session\"."},
    "refuse_delete": {"zh": "{dest} 不是已安装的 {name} 技能，拒绝删除。", "ja": "{dest} はインストール済みの {name} スキルではないため、削除しません。", "en": "{dest} is not an installed {name} skill; refusing to delete."},
    "removed": {"zh": "已删除 {dest}", "ja": "{dest} を削除しました", "en": "removed {dest}"},
}
