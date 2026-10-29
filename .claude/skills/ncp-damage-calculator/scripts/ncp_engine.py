#!/usr/bin/env python
"""Embed the vendored NCP calculator in-process via quickjs-ng — the Node-free calc runtime.

The two CLIs (`ncp-calc-api.py` / `ncp-speedline-api.py`) run the SAME vendored JavaScript the Node
CLIs run (`ncp-calc-api.js` + `script_res/**`), but host it in a quickjs-ng context instead of Node,
so the skill needs no external Node runtime — only `pip install quickjs-ng` (a 500 KB wheel, far
lighter than a Node install; the Node `.js` CLIs remain as an automatic fallback for environments
without quickjs). The vendored engine is upstream-synchronized NCP plus the declared Champions patch
(see conventions.md §1) and is validated
against Node and the independent EXO oracle by the calc contract/online tests.

How the JS is hosted (all shims are load-time only; the on-disk vendored files are never modified):
- Node builtins the wrapper require()s — fs/path/vm/require/module/process — are stubbed. `vm.create
  Context` folds the wrapper's sandbox object onto the single quickjs global (so `ctx === globalThis`,
  matching Node's vm semantics where the context object IS the sandbox global); `vm.runInContext` does
  a global eval so each vendored `var`/`function` becomes global exactly like Node's runInContext.
- Node's vm shares ONE global lexical scope across every script in a context, so a top-level `const`
  in one vendored file is visible to a later file (e.g. TYPE_CHANGE_BOOST_ABILITIES, defined const in
  damage_MASTER.js, used in damage_SV.js/damage_xy.js). quickjs evals each file in its own scope where
  top-level const/let do NOT go global, so COLUMN-0 const/let is normalized to var (in-function,
  indented declarations are untouched). This mirrors Node's shared-global-lexical-scope behaviour.
- Node's readFileSync(p,'utf8') replaces invalid byte sequences with U+FFFD rather than raising; we
  match that so a stray non-UTF-8 byte in a move description never breaks the load.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import quickjs                      # quickjs-ng (drop-in for the archived `quickjs`); optional dep
    QUICKJS_AVAILABLE = True
except Exception:                       # noqa: BLE001 — any import failure means "fall back to Node"
    quickjs = None                      # type: ignore[assignment]
    QUICKJS_AVAILABLE = False

SCRIPTS = Path(__file__).resolve().parent
CALC_API = SCRIPTS / "ncp-calc-api.js"
SPEED_API = SCRIPTS / "ncp-speedline-api.js"
INFERENCE_CORE = SCRIPTS / "inference_core.bundle.js"


class EngineUnavailable(RuntimeError):
    """quickjs-ng is not installed, so the in-process runtime cannot start (caller falls back to Node)."""


# COLUMN-0 (top-level) const/let -> var, replicating Node vm's cross-script shared lexical scope.
_TOPLEVEL_LEXICAL = re.compile(r"(?m)^(const|let)(\s)")


def _match_node_vm_scope(code: str) -> str:
    return _TOPLEVEL_LEXICAL.sub(r"var\2", code)


def _read_js(path: Path, *, vm_scope: bool = False) -> str:
    code = path.read_bytes().decode("utf-8", errors="replace")   # Node readFileSync(p,'utf8') parity
    return _match_node_vm_scope(code) if vm_scope else code


# JS bootstrap: Node-builtin stubs (fs/path/vm/require/module/process/console/__dirname). __read_file
# is a Python callback so the wrapper's own loadCalculator() reads the vendored files off disk verbatim.
_BOOTSTRAP = r"""
globalThis.__dirname = __SCRIPTS__;
globalThis.__filename = __SCRIPTS__ + '/ncp-calc-api.js';
if (typeof console === 'undefined') { globalThis.console = {}; }
['log','error','warn','info','debug'].forEach(function(k){ if(!console[k]) console[k]=function(){}; });
globalThis.module = { exports: {} };
var __MODULE_SENTINEL = {};
globalThis.process = { argv: [], stdout: { write: function(){} }, stderr: { write: function(){} },
                       exit: function(){}, stdin: 0 };
globalThis.require = function(name){
  if (name === 'fs') return { readFileSync: function(p, enc){ return __read_file(String(p)); } };
  if (name === 'path') return {
    join: function(){ return Array.prototype.slice.call(arguments).join('/'); },
    resolve: function(){ return Array.prototype.slice.call(arguments).join('/'); } };
  if (name === 'vm') return {
    createContext: function(o){
      if (o) { for (var k in o) { try { globalThis[k] = o[k]; } catch(e){} } }
      globalThis.window = globalThis;
      return globalThis;
    },
    runInContext: function(code, ctx, opts){ return (0, eval)(code); } };
  if (name === 'readline') return { createInterface: function(){ return { on: function(){}, close: function(){} }; } };
  if (name.indexOf('ncp-calc-api') !== -1) return globalThis.__calc_exports || globalThis.module.exports;
  throw new Error('stub require: unknown module ' + name);
};
globalThis.require.main = __MODULE_SENTINEL;  // != module, so `if (require.main===module) main()` no-ops
"""

# Load the calculator ONCE (the wrapper's own 16-file load + Champions gen wiring), reuse per call, and
# expose a main()-equivalent that returns {output, exit} (mirrors ncp-calc-api.js main()'s try/catch).
_CALC_ENTRY = r"""
globalThis.__CTX = loadCalculator();
globalThis.__run_main = function(command, inputJson, kind){
  var input;
  try {
    input = JSON.parse(inputJson);
    var k = (kind === '' || kind == null) ? undefined : kind;
    var output = runCommand(command, input, globalThis.__CTX, k);
    var exit = (output && !Array.isArray(output) && output.ok === false) ? 1 : 0;
    return JSON.stringify({ output: output, exit: exit });
  } catch (e) {
    var msg = String(e && e.message || e);
    return JSON.stringify({ output: errorObj(msg, undefined, queryOf(input, msg)), exit: 1 });
  }
};
globalThis.__schema_json = function(){ return JSON.stringify(SCHEMA); };
"""


def _wrap_speed_module(src: str) -> str:
    """speedline is a CommonJS module that require()s calc-api. Wrap it in the standard CJS IIFE so its
    top-level `const fs`/`const SCHEMA`/`function resolveNames` stay MODULE-LOCAL (calc-api already put
    same-named consts in the global lexical scope; a bare eval collides: "redeclaration of 'fs'"). The
    append exposes the internals the entry needs (compareSpeed/resolveNames aren't in module.exports)."""
    if src.startswith("#!"):                       # shebang is only tolerated at input pos 0, not mid-IIFE
        src = src.split("\n", 1)[1]
    return ("globalThis.__speed_mod = { exports: {} };\n"
            "(function(module, exports, require, __dirname, __filename){\n"
            + src +
            "\n; module.exports.speedOne = speedOne; module.exports.compareSpeed = compareSpeed;"
            " module.exports.speedTable = speedTable; module.exports.resolveNames = resolveNames;"
            " module.exports.SPEED_SCHEMA = SCHEMA;\n"
            "})(globalThis.__speed_mod, globalThis.__speed_mod.exports, globalThis.require,"
            " globalThis.__dirname, globalThis.__filename);\n"
            "globalThis.__speed_exports = globalThis.__speed_mod.exports;\n")

_SPEED_ENTRY = r"""
globalThis.__run_speed_main = function(command, inputJson){
  var input;
  try {
    input = inputJson.trim() ? JSON.parse(inputJson) : {};
    var ctx = globalThis.__CTX;
    var S = globalThis.__speed_exports;
    var output;
    if (command === 'resolve') output = S.resolveNames(input, ctx);
    else if (command === 'compare') output = S.compareSpeed(input, ctx);
    else if (command === 'batch') output = input.map(function(i, idx){
        try { return S.speedOne(i, ctx); }
        catch(e){ return globalThis.__calc_exports.errorObj(String(e && e.message || e), idx, i && i.name); } });
    else if (command === 'table') output = S.speedTable(input, ctx);
    else output = S.speedOne(input, ctx);
    return JSON.stringify({ output: output, exit: 0 });
  } catch (e) {
    var msg = String(e && e.message || e);
    var q = (input && !Array.isArray(input)) ? input.name : undefined;
    return JSON.stringify({ output: globalThis.__calc_exports.errorObj(msg, undefined, q), exit: 1 });
  }
};
globalThis.__speed_schema_json = function(){ return JSON.stringify(globalThis.__speed_exports.SPEED_SCHEMA); };
"""


class Engine:
    """A resident quickjs-ng context hosting the vendored calculator. Build once, reuse per call
    (the per-calc state the wrapper mutates — _auraState, lazy key indices — is deterministic)."""

    def __init__(self, *, with_speed: bool = False, with_inference: bool = False):
        if not QUICKJS_AVAILABLE:
            raise EngineUnavailable("quickjs-ng is not installed")
        self.ctx = quickjs.Context()
        self.ctx.add_callable("__read_file", self._read_file)
        self.ctx.eval(_BOOTSTRAP.replace("__SCRIPTS__", json.dumps(str(SCRIPTS).replace("\\", "/"))))
        self.ctx.eval(_read_js(CALC_API))                 # the wrapper, verbatim (RegExp.$1 fixed on disk)
        self.ctx.eval(_CALC_ENTRY)                        # runs loadCalculator() (16 vendored files) here
        self._run_main = self.ctx.get("__run_main")
        self._run_speed_main = None
        self._run_inference = None
        if with_speed:
            self.ctx.eval("globalThis.__calc_exports = module.exports;")
            self.ctx.eval(_wrap_speed_module(SPEED_API.read_text(encoding="utf-8")))
            self.ctx.eval(_SPEED_ENTRY)
            self._run_speed_main = self.ctx.get("__run_speed_main")
        if with_inference:
            self.ctx.eval(_read_js(INFERENCE_CORE))
            self.ctx.eval("globalThis.__run_inference = function(raw) { "
                          "return JSON.stringify(QuarkInferenceCore.runModel(JSON.parse(raw))); };")
            self._run_inference = self.ctx.get("__run_inference")

    @staticmethod
    def _read_file(path: str) -> str:
        p = Path(path)
        if not p.is_absolute():
            p = SCRIPTS / path
        return _read_js(p, vm_scope=True)

    def run_calc(self, command: str, payload_json: str, kind: str = "") -> dict:
        """Return {"output": <result>, "exit": <code>} for a calc command, mirroring the JS main()."""
        return json.loads(self._run_main(command, payload_json, kind or ""))

    def run_speed(self, command: str, payload_json: str) -> dict:
        return json.loads(self._run_speed_main(command, payload_json))

    def run_inference(self, payload_json: str) -> object:
        if self._run_inference is None:
            raise RuntimeError("inference core was not loaded")
        return json.loads(self._run_inference(payload_json))

    def calc_schema(self) -> object:
        return json.loads(self.ctx.eval("__schema_json()"))

    def speed_schema(self) -> object:
        return json.loads(self.ctx.eval("__speed_schema_json()"))
