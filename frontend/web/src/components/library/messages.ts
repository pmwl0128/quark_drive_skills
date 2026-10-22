/** Copy of the teams page. It lives in the page's lazy chunk (i18n `defineMessages`), so none of it
 * weighs on the entry bundle; shared keys still resolve through the same `t`. */
import { defineMessages } from "../../i18n.ts";
import { SHARED_MESSAGES } from "../messages.ts";

const LIBRARY_MESSAGES = {
  ...SHARED_MESSAGES,
  "lib.title": { zh: "宝可梦存储", en: "Pokémon Storage", ja: "ポケモンボックス" },
  "lib.lead": {
    zh: "队伍和宝可梦只保存在当前浏览器里，不会上传到服务器。",
    en: "Teams and Pokémon are kept in this browser only and are never uploaded.",
    ja: "チームとポケモンはこのブラウザにだけ保存され、サーバーには送信されません。" },
  "lib.sections": { zh: "存储页面", en: "Storage sections", ja: "ボックスページ" },
  "lib.tab.teams": { zh: "我的队伍", en: "My teams", ja: "マイチーム" },
  "lib.tab.box": { zh: "宝可梦箱", en: "Pokémon Box", ja: "ボックス" },

  "lib.formatPick": { zh: "赛制", en: "Format", ja: "ルール" },
  "lib.paste": { zh: "输入队伍或队伍码", en: "Enter a team or team code", ja: "チーム・チームコードを入力" },
  "lib.code.label": { zh: "队伍码导入", en: "Import a team code", ja: "チームコード読込" },
  "lib.import.replaceTitle": { zh: "替换当前整队", en: "Replace the whole team", ja: "チーム全体を置き換え" },
  "lib.import.replaceBody": { zh: "导入后会替换当前整队。请先保存需要保留的配置。", en: "Import replaces the whole current team. Save any builds you want to keep first.", ja: "読込後、現在のチーム全体を置き換えます。残したい型は先に保存してください。" },
  "lib.import.replaceConfirm": { zh: "确认并导入", en: "Confirm and import", ja: "確認して読み込む" },
  "lib.code.placeholder": { zh: "输入十位公开队伍码", en: "Enter one ten-character public code", ja: "公開10桁コードを入力" },
  "lib.code.loading": { zh: "查询中…", en: "Looking up…", ja: "検索中…" },
  "lib.code.import": { zh: "查询并导入", en: "Look up and import", ja: "検索して読み込む" },
  "lib.code.failed": { zh: "暂时无法导入，请检查队伍码或稍后再试。原有配置已保留。", en: "Unable to import. Check the code or try again later. Your existing sets are unchanged.", ja: "読込できません。コードを確認するか、後ほどお試しください。既存の構成は保持されています。" },
  "lib.firstRun": {
    zh: "输入一个公开队伍码或粘贴 Showdown 文本，预览后保存队伍，成员会同时放进箱子。",
    en: "Enter one public team code or paste Showdown, then preview and save the team. Its Pokémon go into your boxes too.",
    ja: "公開コードを1つ、またはShowdownテキストを入力し、確認後に保存します。メンバーはボックスにも入ります。" },
  "lib.slot": { zh: "队伍位 {n}", en: "Team slot {n}", ja: "チーム枠 {n}" },
  "lib.slot.empty": { zh: "空队伍位", en: "Empty slot", ja: "空き枠" },
  "lib.slot.pasteHere": { zh: "粘贴队伍到这里", en: "Paste a team here", ja: "ここにチームを貼り付け" },
  "lib.slot.more": { zh: "再开 {n} 个队伍位", en: "Open {n} more team slots", ja: "チーム枠を {n} 個追加" },
  "lib.slot.full": {
    zh: "{format}的队伍位都满了。在保存时选一个队伍位替换，或先删掉一支队伍。",
    en: "Every {format} team slot is taken. Pick one to replace when saving, or delete a team first.",
    ja: "{format}のチーム枠はすべて埋まっています。保存時に置き換える枠を選ぶか、先にチームを削除してください。" },
  "lib.team.noMembers": { zh: "没有成员", en: "No members", ja: "メンバーなし" },
  "lib.team.noMembersBody": {
    zh: "这支队伍的宝可梦都已从箱子里删除。可以删掉它，或在这个队伍位粘贴一支新队伍。",
    en: "Every Pokémon of this team has been deleted from the boxes. Delete it, or paste a new team into this slot.",
    ja: "このチームのポケモンはすべてボックスから削除されました。チームを削除するか、この枠に新しいチームを貼り付けてください。" },
  "lib.andMore": { zh: "{names} 等", en: "{names} and more", ja: "{names} ほか" },
  "lib.updated": { zh: "{date} 更新", en: "Updated {date}", ja: "{date} 更新" },
  "lib.ruleChanged": { zh: "规则已变更", en: "Rule changed", ja: "ルール変更あり" },
  "lib.ruleChangedTip": {
    zh: "保存时为 {saved}，当前为 {current}。诊断会按当前规则重新校验。",
    en: "Saved under {saved}; the current rule is {current}. A diagnosis checks it against the current rule.",
    ja: "保存時は {saved}、現在は {current} です。診断で現在のルールに照らして確認できます。" },

  "lib.origin.manual": { zh: "手动添加", en: "Added by hand", ja: "手入力" },
  "lib.origin.import": { zh: "导入", en: "Imported", ja: "インポート" },
  "lib.origin.builder": { zh: "建队向导", en: "Team builder", ja: "構築ウィザード" },
  "lib.origin.diagnose": { zh: "队伍诊断", en: "Diagnosis", ja: "チーム診断" },
  "lib.origin.session": { zh: "建队会话", en: "Team session", ja: "構築セッション" },
  "lib.origin.calc": { zh: "计算器", en: "Calculator", ja: "計算機" },
  "lib.origin.matchup": { zh: "模拟", en: "Simulation", ja: "シミュレーション" },
  "lib.origin.meta": { zh: "环境配置", en: "Metagame build", ja: "環境の型" },

  "lib.act.edit": { zh: "编辑", en: "Edit", ja: "編集" },
  "lib.act.duplicate": { zh: "复制到空队伍位", en: "Copy to an empty slot", ja: "空き枠にコピー" },
  "lib.act.moveUp": { zh: "上移", en: "Move up", ja: "上へ" },
  "lib.act.moveDown": { zh: "下移", en: "Move down", ja: "下へ" },
  "lib.act.delete": { zh: "删除", en: "Delete", ja: "削除" },
  "lib.act.clear": { zh: "清空", en: "Clear", ja: "空にする" },
  "lib.slot.delete": { zh: "删除队伍位", en: "Delete this slot", ja: "チーム枠を削除" },
  "lib.toast.slotDeleted": { zh: "已删除队伍位 {n}", en: "Deleted team slot {n}", ja: "チーム枠 {n} を削除しました" },
  "lib.slot.deleteDefault": {
    zh: "默认队伍位不能删除", en: "The starting slots cannot be deleted", ja: "最初のチーム枠は削除できません" },
  "lib.slot.deleteTitle": { zh: "删除队伍位 {n}", en: "Delete team slot {n}", ja: "チーム枠 {n} を削除" },
  "lib.slot.deleteBody": {
    zh: "队伍位 {n} 里有队伍「{name}」，删除队伍位会一并删除这支队伍（宝可梦仍留在箱子里）。这一步不能撤销。",
    en: "Slot {n} holds the team “{name}”. Deleting the slot deletes the team too (its Pokémon stay in the box). This cannot be undone.",
    ja: "チーム枠 {n} にはチーム「{name}」があります。枠を削除するとチームも削除されます（ポケモンはボックスに残ります）。元に戻せません。" },
  "lib.act.file": { zh: "资料文件", en: "Library file", ja: "ライブラリファイル" },
  "lib.act.fileHint": { zh: "可在别处导入", en: "To import elsewhere", ja: "別の場所で読み込み用" },
  "lib.copyName": { zh: "{name}（副本）", en: "{name} (copy)", ja: "{name}（コピー）" },

  "lib.toast.saved": { zh: "已保存到队伍位 {n}", en: "Saved to team slot {n}", ja: "チーム枠 {n} に保存しました" },
  "lib.toast.replaced": { zh: "已替换队伍位 {n}", en: "Replaced team slot {n}", ja: "チーム枠 {n} を置き換えました" },
  "lib.toast.deleted": { zh: "已删除「{name}」", en: "Deleted “{name}”", ja: "「{name}」を削除しました" },
  "lib.toast.undo": { zh: "撤销", en: "Undo", ja: "元に戻す" },
  "lib.toast.duplicated": { zh: "已复制到队伍位 {n}", en: "Copied to team slot {n}", ja: "チーム枠 {n} にコピーしました" },
  "lib.toast.noSlot": {
    zh: "没有空的队伍位了", en: "There is no empty team slot", ja: "空いているチーム枠がありません" },
  "lib.toast.noCell": { zh: "箱子已满，放不回去了", en: "The boxes are full; it can't go back", ja: "ボックスがいっぱいで戻せません" },
  "lib.toast.imported": {
    zh: "已导入 {teams} 支队伍、{box} 只宝可梦", en: "Imported {teams} teams and {box} Pokémon",
    ja: "{teams} チームと {box} 匹を読み込みました" },
  "lib.toast.importedNoRoom": {
    zh: "已导入 {teams} 支队伍、{box} 只宝可梦；{n} 条因为没有空位没有导入",
    en: "Imported {teams} teams and {box} Pokémon; {n} entries found no room and were left out",
    ja: "{teams} チームと {box} 匹を読み込みました。空きがなかった {n} 件は読み込んでいません" },
  "lib.toast.failed": {
    zh: "没有保存成功，请检查配置与存储权限后重试", en: "Could not save. Check the build and storage permissions, then retry",
    ja: "保存できませんでした。型と保存先の権限を確認してください" },

  "lib.broken": {
    zh: "有 {n} 条记录无法读取（可能由更新版本写入，或已损坏）。原始记录仍保留，“导出全部”会一并带走。",
    en: "{n} stored records can't be read (written by a newer version, or damaged). The originals are kept, and “Export all” includes them.",
    ja: "読み取れない記録が {n} 件あります（新しいバージョンで書き込まれたか、破損しています）。元の記録は保持され、「すべて書き出す」に含まれます。" },
  "lib.loading": { zh: "正在读取本地资料…", en: "Reading the local library…", ja: "ローカルデータを読み込み中…" },
  "lib.unavailable": {
    zh: "无法读取资料库，请检查存储权限后重试。",
    en: "The library cannot be read. Check storage permissions and retry.",
    ja: "ライブラリを読み込めません。保存先の権限を確認して再試行してください。" },

  "lib.edit.team": { zh: "编辑队伍", en: "Edit team", ja: "チームを編集" },
  "lib.edit.box": { zh: "编辑宝可梦", en: "Edit Pokémon", ja: "ポケモンを編集" },
  "lib.field.name": { zh: "队伍名称", en: "Team name", ja: "チーム名" },
  "lib.field.namePlaceholder": {
    zh: "留空则显示成员名称", en: "Leave blank to show the members", ja: "空欄ならメンバー名を表示" },
  "lib.field.nickname": { zh: "昵称", en: "Nickname", ja: "ニックネーム" },
  "lib.field.notes": { zh: "备注", en: "Notes", ja: "メモ" },
  "lib.field.tags": { zh: "标签", en: "Tags", ja: "タグ" },
  "lib.field.tagsHint": { zh: "用逗号分隔", en: "Separate with commas", ja: "カンマ区切り" },
  "lib.save": { zh: "保存", en: "Save", ja: "保存" },
  "lib.cancel": { zh: "取消", en: "Cancel", ja: "キャンセル" },

  "lib.box.prev": { zh: "上一个箱子", en: "Previous box", ja: "前のボックス" },
  "lib.box.next": { zh: "下一个箱子", en: "Next box", ja: "次のボックス" },
  "lib.box.pick": { zh: "箱子", en: "Boxes", ja: "ボックス" },
  "lib.box.open": { zh: "新开箱子", en: "Open a new box", ja: "ボックスを追加" },
  "lib.box.rename": { zh: "重命名箱子", en: "Rename box", ja: "ボックス名を変更" },
  "lib.box.nameField": { zh: "箱子名称", en: "Box name", ja: "ボックス名" },
  "lib.box.total": { zh: "共 {n} 只 · {s} 种", en: "{n} Pokémon · {s} species", ja: "計 {n} 匹 · {s} 種" },
  "lib.box.cell": { zh: "第 {n} 格", en: "Cell {n}", ja: "{n} 番目" },
  "lib.box.cellEmpty": { zh: "第 {n} 格 · 空", en: "Cell {n} · empty", ja: "{n} 番目 · 空き" },
  "lib.box.inTeams": { zh: "所在队伍", en: "In teams", ja: "所属チーム" },
  "lib.box.teamRef": { zh: "{format} {n}", en: "{format} {n}", ja: "{format} {n}" },
  "lib.box.inTeamMark": { zh: "在队伍中", en: "In a team", ja: "チームに登録中" },
  "lib.box.move": { zh: "移动宝可梦", en: "Move a Pokémon", ja: "ポケモンを移動" },
  "lib.box.movePick": {
    zh: "点选或拖框选中要移动的宝可梦（Ctrl / Shift 加选），再点目标格子；可以先切换箱子。",
    en: "Click or drag a frame over the Pokémon to move (Ctrl / Shift adds), then click the target cell; switch boxes first if needed.",
    ja: "移動するポケモンをクリックまたは枠で選び（Ctrl / Shift で追加）、移動先のマスを選んでください。ボックスを切り替えても構いません。" },
  "lib.box.movingHeld": {
    zh: "已选 {n} 只：点目标格子放下（多只保持相对位置），再点已选的可取消。",
    en: "{n} picked: click the target cell to put them down (a group keeps its shape); click a picked one to drop it.",
    ja: "{n} 匹を選択中：移動先のマスをクリック（複数は並びを保持）。選択済みをクリックすると外れます。" },
  "lib.box.moveDone": { zh: "结束移动", en: "Done", ja: "移動を終了" },
  "lib.box.sendTo": { zh: "或放进：", en: "Or send to:", ja: "または送る：" },
  "lib.box.sent": { zh: "已把 {n} 只放进「{name}」", en: "Sent {n} to “{name}”", ja: "{n} 匹を「{name}」に入れました" },
  "lib.box.sendNoRoom": {
    zh: "「{name}」的空位不够", en: "“{name}” has too few free cells", ja: "「{name}」の空きが足りません" },
  "lib.box.groupNoRoom": {
    zh: "放不下：目标位置超出箱子，或已有其他宝可梦。",
    en: "No room: the group would leave the box or land on other Pokémon.",
    ja: "置けません：ボックスからはみ出すか、別のポケモンと重なります。" },
  "lib.stats.base": { zh: "种族值", en: "Base", ja: "種族値" },
  "lib.stats.actual": { zh: "能力值", en: "Lv.50", ja: "実数値" },
  "lib.stats.total": { zh: "种族值合计", en: "Base total", ja: "種族値合計" },
  "lib.box.delete": { zh: "删除箱子", en: "Delete box", ja: "ボックスを削除" },
  "lib.box.deleteDefault": {
    zh: "默认箱子不能删除", en: "The starting boxes cannot be deleted", ja: "最初のボックスは削除できません" },
  "lib.box.deleteTitle": { zh: "删除「{name}」", en: "Delete “{name}”", ja: "「{name}」を削除" },
  "lib.box.deleteBody": {
    zh: "箱子里还有 {n} 只宝可梦，会一并删除，并从所在的队伍里移出。这一步不能撤销。",
    en: "It still holds {n} Pokémon. They are deleted with it and taken out of their teams. This cannot be undone.",
    ja: "まだ {n} 匹のポケモンがいます。一緒に削除され、所属チームからも外れます。元に戻せません。" },
  "lib.box.deleted": { zh: "已删除箱子「{name}」", en: "Deleted the box “{name}”", ja: "ボックス「{name}」を削除しました" },
  "lib.detail.saving": { zh: "保存中…", en: "Saving…", ja: "保存中…" },
  "lib.detail.saved": { zh: "已自动保存", en: "Saved", ja: "自動保存済み" },
  "lib.detail.failed": { zh: "保存失败", en: "Could not save", ja: "保存できませんでした" },
  "lib.box.moving": {
    zh: "点选目标格子，可以先切换箱子；目标格已有宝可梦时两者互换。",
    en: "Pick the target cell — switch boxes first if needed. A Pokémon already there swaps places.",
    ja: "移動先のマスを選んでください。ボックスを切り替えても構いません。移動先にポケモンがいる場合は入れ替わります。" },
  "lib.box.add": { zh: "添加", en: "Add", ja: "追加" },
  "lib.box.addLabel": { zh: "添加一只宝可梦", en: "Add a Pokémon", ja: "ポケモンを追加" },
  "lib.box.addPlaceholder": { zh: "输入宝可梦名称", en: "Type a Pokémon name", ja: "ポケモン名を入力" },
  "lib.box.fromText": { zh: "从文本添加", en: "Add from text", ja: "テキストから追加" },
  "lib.box.fromTextHint": {
    zh: "Showdown 文本里的宝可梦会从这一格开始依次放入。",
    en: "Pokémon in a Showdown text are placed from this cell onwards.",
    ja: "Showdown テキストのポケモンをこのマスから順に入れます。" },
  "lib.box.added": { zh: "已放入箱子：{name}", en: "Put in the box: {name}", ja: "ボックスに入れました：{name}" },
  "lib.box.addedMany": { zh: "已放入 {n} 只宝可梦", en: "Put {n} Pokémon in the boxes", ja: "{n} 匹をボックスに入れました" },
  "lib.box.addedSome": {
    zh: "已放入 {n} 只宝可梦；箱子满了，还有 {left} 只没放进去",
    en: "Put {n} Pokémon in the boxes; they are full, {left} were left out",
    ja: "{n} 匹を入れました。ボックスがいっぱいのため {left} 匹は入れていません" },
  "lib.box.unknown": { zh: "找不到这只宝可梦", en: "No such Pokémon", ja: "該当するポケモンがいません" },
  "lib.box.full": { zh: "箱子都满了", en: "Every box is full", ja: "ボックスがすべていっぱいです" },
  "lib.box.deletedFromTeams": {
    zh: "已删除「{name}」，并从 {n} 支队伍中移除", en: "Deleted “{name}” and took it out of {n} teams",
    ja: "「{name}」を削除し、{n} チームから外しました" },

  "lib.paste.hint": {
    zh: "输入一个十位公开队伍码，或粘贴 Showdown / Pokepaste 文本，名称支持中、日、英。",
    en: "Enter one ten-character public team code or paste Showdown / Pokepaste text. Names may be in English, Chinese or Japanese.",
    ja: "公開10桁コードを1つ、またはShowdown / Pokepasteテキストを入力してください。中・日・英名に対応。" },
  "lib.paste.text": { zh: "队伍文本或队伍码", en: "Team text or code", ja: "チームテキスト・コード" },
  "lib.paste.read": { zh: "读取", en: "Read", ja: "読み取る" },
  "lib.paste.reading": { zh: "读取中…", en: "Reading…", ja: "読み取り中…" },
  "lib.paste.none": {
    zh: "没有识别出任何宝可梦。", en: "No Pokémon were recognised.", ja: "ポケモンを認識できませんでした。" },
  "lib.paste.unresolved": {
    zh: "以下名称未能识别，已略过：{names}", en: "Not recognised, left out: {names}",
    ja: "次の名前は認識できず、省略しました：{names}" },
  "lib.paste.rescaled": {
    zh: "文本里的努力值已按 SP 上限换算。", en: "The EVs in the text were rescaled onto the SP budget.",
    ja: "テキストの努力値はSPの上限に合わせて換算しました。" },
  "lib.paste.limit": {
    zh: "一支队伍最多 6 只，只保留了前 6 只。", en: "A team holds at most six; only the first six were kept.",
    ja: "1チームは最大6匹です。先頭の6匹だけを残しました。" },
  "lib.paste.reuse": { zh: "箱子里已有", en: "Already boxed", ja: "ボックスにあり" },
  "lib.paste.target": { zh: "存到", en: "Save to", ja: "保存先" },
  "lib.paste.cells": {
    zh: "会新占用 {n} 格箱子（还剩 {free} 格），配置相同的宝可梦直接复用。",
    en: "Takes {n} new box cells ({free} free); Pokémon with the same build already boxed are reused.",
    ja: "ボックスを新たに {n} マス使います（残り {free} マス）。同じ型のポケモンはそのまま使います。" },
  "lib.paste.noRoom": {
    zh: "箱子空位不够：需要 {n} 格，只剩 {free} 格。先删掉一些宝可梦再保存。",
    en: "Not enough room in the boxes: {n} cells needed, {free} free. Delete some Pokémon first.",
    ja: "ボックスの空きが足りません：{n} マス必要ですが、残りは {free} マスです。先にポケモンを削除してください。" },
  "lib.paste.slotTaken": {
    zh: "队伍位 {n} 里已有「{name}」。保存后它会被替换，它的宝可梦仍留在箱子里。",
    en: "Team slot {n} holds “{name}”. Saving replaces it; its Pokémon stay in the boxes.",
    ja: "チーム枠 {n} には「{name}」があります。保存すると置き換わりますが、そのポケモンはボックスに残ります。" },
  "lib.paste.duplicate": {
    zh: "同样的队伍已经保存在{format}队伍位 {n}。", en: "The same team is already saved in {format} slot {n}.",
    ja: "同じチームが{format}のチーム枠 {n} に保存されています。" },
  "lib.paste.saveTo": { zh: "保存到队伍位 {n}", en: "Save to slot {n}", ja: "枠 {n} に保存" },
  "lib.paste.replace": { zh: "替换队伍位 {n}", en: "Replace slot {n}", ja: "枠 {n} を置き換え" },
  "lib.paste.boxRoom": {
    zh: "箱子还剩 {free} 格，只会放入前 {free} 只。", en: "{free} cells are free; only the first {free} will go in.",
    ja: "空きは {free} マスのため、先頭の {free} 匹だけを入れます。" },
  "lib.paste.addBox": { zh: "放入箱子（{n}）", en: "Put in the box ({n})", ja: "ボックスに入れる（{n}）" },
  "lib.paste.edit": { zh: "修改文本", en: "Edit text", ja: "テキストを修正" },

  "lib.import": { zh: "导入文件", en: "Import file", ja: "ファイルを読み込む" },
  "lib.exportAll": { zh: "导出全部", en: "Export all", ja: "すべて書き出す" },
  "lib.import.title": { zh: "导入资料文件", en: "Import a library file", ja: "ファイルを読み込む" },
  "lib.import.hint": {
    zh: "选择从本站“导出全部”或队伍“导出 → 资料文件”得到的 .json 文件。Showdown 文本请使用“粘贴队伍”。",
    en: "Choose a .json file saved with “Export all” or a team's “Export → Library file” on this site. For Showdown text, use “Paste a team”.",
    ja: "このサイトの「すべて書き出す」またはチームの「書き出し → ライブラリファイル」で保存した .json ファイルを選んでください。Showdown テキストは「チームを貼り付け」から。" },
  "lib.import.choose": { zh: "选择文件", en: "Choose a file", ja: "ファイルを選択" },
  "lib.import.notJson": {
    zh: "这个文件不是 JSON，无法读取。", en: "This file is not JSON.", ja: "このファイルは JSON ではありません。" },
  "lib.import.notLibrary": {
    zh: "这不是本站导出的资料文件。", en: "This is not a library file exported from this site.",
    ja: "このサイトから書き出したファイルではありません。" },
  "lib.import.teams": { zh: "队伍 {n} 支", en: "{n} teams", ja: "チーム {n} 件" },
  "lib.import.dup": { zh: "其中 {n} 支已经保存过", en: "{n} of them already saved", ja: "うち {n} 件は保存済み" },
  "lib.import.box": { zh: "宝可梦 {n} 只", en: "{n} Pokémon", ja: "ポケモン {n} 匹" },
  "lib.import.dupBox": { zh: "其中 {n} 只已在箱子里", en: "{n} of them already boxed", ja: "うち {n} 匹はボックスにあり" },
  "lib.import.invalid": {
    zh: "{n} 条无法读取，将略过", en: "{n} unreadable entries will be skipped", ja: "読み取れない {n} 件は省略します" },
  "lib.import.room": {
    zh: "当前箱子剩 {box} 格，单打队伍位剩 {single} 个、双打剩 {double} 个；放不下的会略过。",
    en: "{box} box cells, {single} singles and {double} doubles team slots are free; whatever doesn't fit is skipped.",
    ja: "空きはボックス {box} マス、シングル枠 {single} 個、ダブル枠 {double} 個です。入りきらないものは省略します。" },
  "lib.import.keepDup": {
    zh: "已经存在的队伍和宝可梦也另存一份", en: "Also keep second copies of what is already stored",
    ja: "保存済みのチームとポケモンも別に保存する" },
  "lib.import.apply": { zh: "导入", en: "Import", ja: "読み込む" },
  "lib.import.nothing": {
    zh: "文件里没有可以导入的记录。", en: "There is nothing to import in this file.",
    ja: "このファイルには読み込める記録がありません。" },

  "lib.store.persisted": { zh: "已获得持久存储权限", en: "Persistent storage granted", ja: "永続ストレージ許可済み" },
  "lib.store.bestEffort": {
    zh: "存储空间紧张时浏览器可能清理", en: "The browser may clear it when space runs low",
    ja: "容量不足時に消去される場合があります" },
  "lib.store.usage": { zh: "本站存储已用 {size}", en: "{size} of site storage used", ja: "サイトの保存容量 {size}" },
  "lib.store.capacity": {
    zh: "已开启 {boxes} 个箱子（{cells} 格）· 单打 {single} 个、双打 {double} 个队伍位",
    en: "{boxes} boxes open ({cells} cells) · {single} singles and {double} doubles team slots",
    ja: "ボックス {boxes} 個（{cells} マス）· シングル {single} 枠、ダブル {double} 枠" },
  "lib.store.origin": {
    zh: "在线资料按浏览器和网址分别保存。更换浏览器、网址或清除站点数据前，请先“导出全部”，再到新地址导入。",
    en: "Online libraries are separate for each browser and web address. Before switching browsers or addresses, or clearing site data, use “Export all” and import the file at the new address.",
    ja: "オンラインのデータはブラウザとURLごとに保存されます。ブラウザやURLの変更、サイトデータの消去の前に「すべて書き出す」で保存し、新しい場所で読み込んでください。" },

  "lib.act.setActive": { zh: "设为当前队伍", en: "Work with this team", ja: "使用中のチームにする" },
  "lib.activeBadge": { zh: "当前", en: "Current", ja: "使用中" },

  "lib.members.add": { zh: "从箱子加入", en: "Add from the boxes", ja: "ボックスから追加" },
  "lib.members.remove": { zh: "移出队伍", en: "Take out of the team", ja: "チームから外す" },
  "lib.remove.title": { zh: "把「{name}」移出队伍", en: "Take “{name}” out of the team", ja: "「{name}」をチームから外す" },
  "lib.remove.ask": {
    zh: "是否同时删除箱子里保存的此宝可梦？", en: "Also delete this Pokémon from its box?",
    ja: "ボックスに保存されているこのポケモンも削除しますか？" },
  "lib.remove.yes": { zh: "是", en: "Yes", ja: "はい" },
  "lib.remove.no": { zh: "否", en: "No", ja: "いいえ" },
  "lib.toast.leftTeam": { zh: "已移出队伍：{name}", en: "Taken out of the team: {name}", ja: "チームから外しました：{name}" },
  "lib.pick.title": { zh: "从箱子挑选", en: "Pick from the boxes", ja: "ボックスから選ぶ" },
  "lib.pick.inTeam": { zh: "已在这支队伍里", en: "Already in this team", ja: "このチームにいます" },
  "lib.pick.empty": {
    zh: "箱子是空的，先在“宝可梦箱”里添加宝可梦。", en: "The boxes are empty; add Pokémon under “Pokémon Box” first.",
    ja: "ボックスは空です。先に「ボックス」でポケモンを追加してください。" },
  "lib.join": { zh: "加入队伍", en: "Add to a team", ja: "チームに入れる" },
  "lib.join.title": { zh: "把「{name}」加入队伍", en: "Add “{name}” to a team", ja: "「{name}」をチームに入れる" },
  "lib.join.new": { zh: "在这里新建队伍", en: "Start a team here", ja: "ここで新しいチームを作る" },
  "lib.join.full": { zh: "已满 6 只", en: "Holds six", ja: "6匹そろっています" },
  "lib.join.present": { zh: "已在队伍里", en: "Already in it", ja: "登録済み" },
  "lib.toast.joined": { zh: "已加入{format}队伍位 {n}", en: "Added to {format} slot {n}", ja: "{format}の枠 {n} に入れました" },
  "lib.toast.started": {
    zh: "已在{format}队伍位 {n} 新建队伍", en: "Started a team in {format} slot {n}",
    ja: "{format}の枠 {n} に新しいチームを作りました" },
  "lib.toast.teamFull": { zh: "这支队伍已经有 6 只了", en: "This team already holds six", ja: "このチームはすでに6匹です" },
  "lib.toast.alreadyIn": { zh: "它已经在这支队伍里了", en: "It is already in this team", ja: "すでにこのチームにいます" },

  "lib.act.editBuild": { zh: "编辑配置", en: "Edit build", ja: "型を編集" },
  "lib.edit.build": { zh: "编辑「{name}」的配置", en: "Edit {name}’s build", ja: "「{name}」の型を編集" },
  "lib.toast.buildSaved": { zh: "已保存配置：{name}", en: "Build saved: {name}", ja: "型を保存しました：{name}" },

  "dock.title": { zh: "我的队伍与宝可梦箱", en: "My teams and Pokémon Box", ja: "マイチームとボックス" },
  "dock.tab.teams": { zh: "我的队伍", en: "My teams", ja: "マイチーム" },
  "dock.tab.box": { zh: "宝可梦箱", en: "Pokémon Box", ja: "ボックス" },
  "dock.openPage": { zh: "在存储页打开", en: "Open the Storage page", ja: "ボックスページで開く" },
  "dock.expand": { zh: "展开", en: "Expand", ja: "広げる" },
  "dock.collapse": { zh: "收起", en: "Shrink", ja: "縮める" },
  "dock.noTeams": {
    zh: "还没有{format}队伍。可以在存储页粘贴，或从下方保存当前页面的队伍。",
    en: "No {format} teams yet. Paste one on the Storage page, or keep this page's team below.",
    ja: "{format}のチームはまだありません。ボックスページで貼り付けるか、下からこのページのチームを保存できます。" },
  "dock.pickTeam": {
    zh: "点一支队伍，把它设为当前队伍。", en: "Pick a team to work with.",
    ja: "使うチームを選んでください。" },
  "dock.emptyBox": {
    zh: "箱子是空的。可以在存储页添加，或从下方保存当前页面的宝可梦。",
    en: "The boxes are empty. Add Pokémon on the Storage page, or keep this page's below.",
    ja: "ボックスは空です。ボックスページで追加するか、下からこのページのポケモンを保存できます。" },
  "dock.toPage": { zh: "填入当前页面", en: "Into this page", ja: "このページへ" },
  "dock.toLibrary": { zh: "从当前页面保存", en: "Keep from this page", ja: "このページから保存" },
  "dock.nothingHere": {
    zh: "这个页面没有可以填入或保存的位置。", en: "This page has nothing to fill or keep.",
    ja: "このページには入れたり保存したりできる場所がありません。" },
  "dock.needTeam": { zh: "先选一支队伍", en: "Pick a team first", ja: "先にチームを選んでください" },
  "dock.needMon": { zh: "先选一只宝可梦", en: "Pick a Pokémon first", ja: "先にポケモンを選んでください" },
  "dock.dragHint": {
    zh: "也可以把队伍或宝可梦直接拖到页面上。", en: "You can also drag a team or a Pokémon onto the page.",
    ja: "チームやポケモンをページへ直接ドラッグすることもできます。" },
  "dock.nothingToKeep": {
    zh: "当前页面还没有可以保存的内容", en: "Nothing on this page to keep yet",
    ja: "このページにはまだ保存できる内容がありません" },
  "dock.saveTeam": { zh: "保存队伍", en: "Keep a team", ja: "チームを保存" },
  "lib.rail.builds": { zh: "环境配置", en: "Builds from", ja: "環境の型" },
  "lib.rail.into": { zh: "放入", en: "Into", ja: "入れる先" },
  "lib.rail.hint": {
    zh: "展开一只宝可梦，点一套环境配置或「自定义」，放进所选箱子的下一个空位；满了顺延到后面的箱子。",
    en: "Open a Pokémon and pick a build, or Custom, to put it in the chosen box's next free cell (later boxes when full).",
    ja: "ポケモンを開いて型か「カスタム」を選ぶと、選んだボックスの次の空きに入ります（満杯なら次のボックスへ）。" },
  "lib.rail.added": { zh: "已放入「{box}」：{name}", en: "Kept {name} in {box}", ja: "「{box}」に{name}を入れました" },
  "lib.rail.full": { zh: "宝可梦箱都满了，先整理或开启新的箱子。", en: "Every box is full. Make room or open another box.",
    ja: "ボックスがすべて満杯です。整理するか新しいボックスを開いてください。" },
  "lib.import.none": {
    zh: "还没有保存{format}队伍。", en: "No {format} teams kept yet.", ja: "{format}のチームはまだ保存されていません。" },
  "lib.import.more": { zh: "展开更多 · 还有 {n} 支", en: "Show {n} more", ja: "残り {n} チームを表示" },
  "lib.import.less": { zh: "收起队伍", en: "Show fewer", ja: "折りたたむ" },
};

export type LibraryMsgKey = keyof typeof LIBRARY_MESSAGES;

export const useLibraryT = defineMessages(LIBRARY_MESSAGES);

/** `{name}`-style placeholders; a missing value leaves its placeholder visible rather than blank. */
export function fill(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (whole, key: string) =>
    key in values ? String(values[key]) : whole);
}
