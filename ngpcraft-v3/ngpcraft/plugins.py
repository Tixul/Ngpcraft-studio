"""GB Studio 4.3.2 event plugins (docs/M7_PLUGINS.md).

A project's `plugins/*/**/events/event*.js` (the glob of GB Studio's
loadScriptEventHandlers.ts) are loaded by GB Studio's OWN isolated loader
(untrustedHandler.ts, QuickJS) in a Node process (plugin_bridge.cjs). Their
`compile(input, helpers)` runs there with GB Studio's own helpers and
ScriptBuilder (deprecated API included); every builder method that the V3
ScriptBuilder implements is served here, on the V3 builder of the script
being compiled. The builder state (output, labels, locals, stack, data
tables) crosses with every call, so the plugin writes into the same script
as the events around it, and the NGPC adaptations of V3 apply.

Engine plugins (C or assembly for the Game Boy engine, engine.json) are not
event plugins: their code cannot run on the NGPC (engine_plugins).

Requirements, only when a project has event plugins (bridge_command), first
found wins:
  - NGPCRAFT_PLUGIN_HOST: the packaged editor, run as
    `<host> --ngpcraft-plugin-bridge` (its own bridge and bundle; set by the
    editor for the V3 it launches);
  - NGPCRAFT_GBSTUDIO_BUNDLE: GB Studio's modules in one file
    (src/lib/forge/hooks/bundle-plugin-bridge.js), run by Node;
  - Node, a GB Studio 4.3.2 source tree (`src`) and node_modules holding its
    QuickJS and TypeScript: NGPCRAFT_NODE, NGPCRAFT_GBSTUDIO_SRC,
    NGPCRAFT_GBSTUDIO_MODULES (defaults: the published reference beside the
    repository and the editor's node_modules).
"""
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .compiler import CompileError
from .gbvm.builder import _UNDEFINED, ScriptBuilder

BRIDGE = Path(__file__).with_name("plugin_bridge.cjs")
# The V3 is the ngpcraft-v3 folder of the NgpCraft Studio repository (the GB
# Studio fork): its node_modules are the editor's. The GB Studio sources must
# be the RELEASED 4.3.2 (the fork changes some of them): outside the
# repository, beside it (as tools/locations.py; NGPCRAFT_GBSTUDIO_REFERENCE).
STUDIO = Path(__file__).resolve().parents[2]
DEFAULT_SRC = (Path(os.environ["NGPCRAFT_GBSTUDIO_REFERENCE"]) / "src"
               if os.environ.get("NGPCRAFT_GBSTUDIO_REFERENCE") else
               STUDIO.parent / "NgpCraft" / "_Engine" / "_reference" / "gb-studio-4.3.2" / "src")
DEFAULT_MODULES = STUDIO / "node_modules"

# ScriptBuilderBase field -> V3 ScriptBuilder attribute (same shapes).
STATE = {"output": "output", "dependencies": "dependencies", "nextLabel": "next_label",
         "labelLookup": "label_lookup", "localsLookup": "locals_lookup", "localsSize": "locals_size",
         "stackPtr": "stack_ptr", "labelStackSize": "label_stack_size", "headers": "headers",
         "dataTables": "data_tables", "actorIndex": "actor_index"}
# Options that are functions on the GB Studio side (the bridge provides them).
_HOST_OPTIONS = {"compileEvents", "warnings", "scriptEventHandlers", "pluginEvents"}


def event_files(root):
    """loadScriptEventHandlers.ts: `${projectRoot}/plugins/*/**/events/event*.js`."""
    plugins = Path(root) / "plugins"
    if not plugins.is_dir():
        return []
    return sorted(p for p in plugins.glob("*/**/events/event*.js") if p.is_file())


def engine_plugins(root):
    """Plugins carrying Game Boy engine code (an engine/ folder or engine.json)."""
    plugins = Path(root) / "plugins"
    if not plugins.is_dir():
        return []
    return sorted(p.name for p in plugins.iterdir()
                  if p.is_dir() and ((p / "engine").exists() or any(p.glob("**/engine.json"))))


def normalize(name):
    """`_addNL` and `_add_nl` meet: leading underscores kept, case and underscores dropped."""
    lead = len(name) - len(name.lstrip("_"))
    return "_" * lead + name[lead:].replace("_", "").lower()


def _arity(fn, skip=1):
    params = list(inspect.signature(fn).parameters.values())[skip:]
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return {"required": sum(p.default is inspect.Parameter.empty for p in positional), "total": len(positional),
            "varargs": any(p.kind == p.VAR_POSITIONAL for p in params)}


def _projectile(args):
    """GB Studio passes a projectile's fields one by one; V3 takes them as one entry."""
    from .projectiles import FIELDS
    return dict(zip(FIELDS, args))


# GB Studio methods whose V3 port takes another shape (signature_audit in docs/M7_PLUGINS.md).
ADAPTERS = {
    "getProjectileIndex": lambda b, *fields: b.get_projectile_index(_projectile(fields)),
    "loadProjectile": lambda b, index, *fields: b.load_projectile(index, _projectile(fields)),
}


# V3 methods that stay GB Studio's: _rpn returns a chainable object (its stop() writes
# through _addCmd, which is V3's).
NOT_SHARED = {"_rpn"}


def v3_methods():
    """Builder methods offered to GB Studio's helpers: {normalized name: {name, arity}},
    adapters under their exact GB Studio name ("@" names, served by ADAPTERS)."""
    methods = {normalize(name): {"name": name, **_arity(value)} for name, value in vars(ScriptBuilder).items()
               if callable(value) and not name.startswith("__") and not isinstance(value, staticmethod)
               and name not in NOT_SHARED}
    for gb_name, adapter in ADAPTERS.items():
        methods[gb_name] = {"name": "@" + gb_name, "required": 0, "total": 0, "varargs": True}
    return methods


def _json(value):
    """A V3 value as JSON for GB Studio (functions dropped, as JSON.stringify does)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if value is _UNDEFINED:
        return {"__undef__": 1}
    if isinstance(value, (list, tuple)):
        return [None if callable(v) else _json(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_json(v) for v in value)
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items() if not callable(v)}
    return None


def bridge_command():
    """(command line of the plugin bridge, missing requirements) — see the module docstring."""
    host = os.environ.get("NGPCRAFT_PLUGIN_HOST")
    if host:
        return [host, "--ngpcraft-plugin-bridge"], [] if Path(host).is_file() else [
            f"the editor (NGPCRAFT_PLUGIN_HOST: {host} not found)"]
    node = os.environ.get("NGPCRAFT_NODE") or shutil.which("node")
    bundle = os.environ.get("NGPCRAFT_GBSTUDIO_BUNDLE")
    if bundle:
        missing = [what for what, ok in (("Node (NGPCRAFT_NODE)", node),
                                         (f"the GB Studio bundle (NGPCRAFT_GBSTUDIO_BUNDLE: {bundle})",
                                          Path(bundle).is_file())) if not ok]
        return [node, str(BRIDGE), "--bundle", bundle], missing
    src = Path(os.environ.get("NGPCRAFT_GBSTUDIO_SRC") or DEFAULT_SRC)
    modules = Path(os.environ.get("NGPCRAFT_GBSTUDIO_MODULES") or DEFAULT_MODULES)
    missing = [what for what, ok in (
        ("Node (NGPCRAFT_NODE)", node),
        ("GB Studio 4.3.2 sources (NGPCRAFT_GBSTUDIO_SRC)",
         (src / "lib/scriptEventsHandlers/untrustedHandler.ts").is_file()),
        ("node_modules with QuickJS and TypeScript (NGPCRAFT_GBSTUDIO_MODULES)",
         (modules / "quickjs-emscripten-core").is_dir() and (modules / "typescript").is_dir()),
    ) if not ok]
    return [node, str(BRIDGE), str(src), str(modules)], missing


class PluginBridge:
    """One Node process per export; serves the plugins' builder calls."""

    def __init__(self, files):
        command, missing = bridge_command()
        if missing:
            raise CompileError("event plugins need " + ", ".join(missing) + " (docs/M7_PLUGINS.md)")
        # Console output of GB Studio or of a plugin: a file, never a pipe nobody drains
        # (a full pipe would block the bridge).
        self.log = tempfile.TemporaryFile()
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.log, text=True, encoding="utf-8")
        self.builder = None
        self.error = None
        self._send({"op": "load", "files": [str(f) for f in files], "methods": v3_methods()})
        loaded = self._receive()
        if loaded.get("op") != "loaded":
            raise CompileError(f"plugin bridge: unexpected start {loaded}")
        self.events = loaded["events"]
        self.events_by_id = {e["id"]: e for e in self.events if "id" in e}

    # -- transport ----------------------------------------------------------
    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def _receive(self):
        line = self.process.stdout.readline()
        while line.strip() == "" and line:      # Electron (plugin host) starts stdout with a blank line on Windows
            line = self.process.stdout.readline()
        if not line:
            self.process.wait()
            self.log.seek(0)
            raise CompileError("plugin bridge stopped: " + self.log.read().decode("utf-8", "replace")[-2000:])
        return json.loads(line)

    def close(self):
        if self.process.poll() is None:
            try:
                self._send({"op": "quit"})
                self.process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
        for stream in (self.process.stdin, self.process.stdout, self.log):
            stream.close()

    # -- builder state --------------------------------------------------------
    def _state(self):
        b = self.builder
        return {js: _json(getattr(b, py)) for js, py in STATE.items()}

    def _apply(self, state):
        b = self.builder
        if not state:
            return
        for message in state.get("warnings", []):
            b.warn(message)
        for js, py in STATE.items():
            if js == "output":
                b.output[:] = state["output"]      # the list the script holds
            else:
                setattr(b, py, state[js])

    # -- values ---------------------------------------------------------------
    def _decode(self, value):
        if isinstance(value, list):
            return [None if v == {"__undef__": 1} else self._decode(v) for v in value]
        if isinstance(value, dict):
            if value == {"__undef__": 1}:
                return _UNDEFINED
            if set(value) == {"__fn__"}:
                fn = value["__fn__"]
                return lambda *args: self._invoke(fn, args)
            return {k: self._decode(v) for k, v in value.items() if v != {"__undef__": 1}}
        return value

    def _invoke(self, fn, args):
        """Call a function the plugin gave (a branch callback) and serve it."""
        self._send({"op": "invoke", "fn": fn, "args": [_json(a) for a in args], "state": self._state()})
        while True:
            m = self._receive()
            if m["op"] in ("returned", "threw"):
                self._apply(m["state"])
                if m["op"] == "threw":
                    raise self._failure(f"plugin callback: {m['message']}")
                return self._decode(m["value"])
            self._serve(m)

    def _failure(self, message):
        """The first builder error raised while serving keeps its type and location."""
        error, self.error = self.error, None
        return error if error is not None else CompileError(message)

    # -- requests from the plugin ---------------------------------------------
    def _call(self, name, args):
        if name.startswith("@"):
            return ADAPTERS[name[1:]](self.builder, *[self._decode(a) for a in args])
        method = getattr(self.builder, name)
        params = list(inspect.signature(method).parameters.values())
        values = [self._decode(a) for a in args]
        from .gbvm.builder import RAW_POSITION_OPS
        from .world import wide
        if wide() and name == "_add_cmd" and values and values[0] in RAW_POSITION_OPS:
            # world.py: the plugin computes GB Studio subpixels for this operand.
            raise CompileError(f"event plugin: {values[0]} written directly carries GB Studio subpixel "
                               "positions, which do not fit 16 bits in a game with a scene over 255 tiles "
                               "(docs/PLAN_GRANDES_CARTES.md)")
        while values and values[-1] is _UNDEFINED and (len(values) > len(params) or
                                                       params[len(values) - 1].default is not inspect.Parameter.empty):
            values.pop()                       # omitted: the default applies
        for i, v in enumerate(values):         # undefined: the default, else None (no value)
            if v is _UNDEFINED:
                values[i] = (params[i].default if i < len(params) and params[i].default is not inspect.Parameter.empty
                             else None)
        return method(*values)

    def _serve(self, m):
        op = m["op"]
        self._apply(m.get("state"))
        try:
            if op == "call":
                value = self._call(m["name"], m["args"])
            elif op == "compileEvents":
                self.builder.options["compileEvents"](self.builder, self._decode(m["events"]))
                value = None
            else:
                raise CompileError(f"plugin bridge: unexpected message {op}")
        except Exception as exc:                # reaches the plugin as a JavaScript exception
            if self.error is None:
                self.error = exc
            self._send({"op": "reply", "error": f"{type(exc).__name__}: {exc}", "state": self._state()})
            return
        self._send({"op": "reply", "value": _json(value), "state": self._state()})

    def compile(self, command, event, builder):
        """The plugin event's compile(input, helpers) on `builder` (compileEventsWithScriptBuilder)."""
        outer, self.builder = self.builder, builder
        try:
            options = {k: _json(v) for k, v in builder.options.items()
                       if k not in _HOST_OPTIONS and not callable(v)}
            self._send({"op": "compile", "id": command,
                        "input": _json({**(event.get("args") or {}), **(event.get("children") or {})}),
                        "event": _json(event), "options": options, "state": self._state()})
            while True:
                m = self._receive()
                if m["op"] in ("done", "failed"):
                    self._apply(m["state"])
                    if m["op"] == "failed":
                        raise self._failure(m["message"])
                    if self.error is not None:  # caught by the plugin: GB Studio would go on too
                        self.error = None
                    return
                self._serve(m)
        finally:
            self.builder = outer


    def oracle(self, events, options, lock=False):
        """Tests: the events compiled by GB Studio's own ScriptBuilder alone (plugin events only)."""
        self._send({"op": "oracle", "events": _json(events), "lock": lock,
                    "options": {k: _json(v) for k, v in options.items() if k not in _HOST_OPTIONS and not callable(v)}})
        m = self._receive()
        if "error" in m:
            raise CompileError(f"GB Studio: {m['error']}")
        return m["text"]


def load(root):
    """({event id: metadata}, bridge or None) for a project's event plugins."""
    files = event_files(root)
    if not files:
        return {}, None
    bridge = PluginBridge(files)
    found = {}
    for event in bridge.events:
        if "error" in event:
            bridge.close()
            where = Path(event["file"]).relative_to(Path(root)).as_posix()
            raise CompileError(f"{where}: plugin event not loaded: {event['error']}")
        found[event["id"]] = event         # eventHandlers[handler.id] = handler: the last one wins
    return found, bridge
