// GB Studio 4.3.2 event plugins compiled by NgpCraft V3 (docs/M7_PLUGINS.md).
//
// The plugin runs in GB Studio's OWN isolated loader (lib/scriptEventsHandlers/
// untrustedHandler.ts: QuickJS, no file system, no network, no process), loaded
// unchanged from a GB Studio source tree, and receives GB Studio's own helpers
// (compileEntityEvents.ts: {LATEST_PROJECT_VERSION, ...options, ...scriptBuilder,
// scriptSymbolName, event}) built on GB Studio's own ScriptBuilder, deprecated
// API included. Every ScriptBuilder method that V3 implements is replaced by a
// call to the V3 ScriptBuilder (Python): the NGPC adaptations apply, and the
// builder state (output, labels, locals, stack) crosses with each call, so both
// builders write one script.
//
// Protocol: one JSON object per line. stdin = messages from Python, stdout =
// messages to Python. Both sides wait synchronously and serve nested requests
// while they wait (callbacks given by a plugin, plugin events inside branches).
//   Python -> Node: load {files, methods}, compile {id, input, event, options, state},
//                   invoke {fn, args, state}, reply {value, state} | {error, state}, quit,
//                   oracle {events, options, lock} (tests: GB Studio alone) -> oracle {text} | {error}
//   Node -> Python: loaded {events}, call {name, args, state}, compileEvents {events, state},
//                   done {state} | failed {message, state}, returned {value, state} | threw {message, state}
// Values: JS undefined is {"__undef__": 1}; a function is {"__fn__": id}.
const fs = require('node:fs');
const path = require('node:path');

// Console output (GB Studio's or a plugin's) must not corrupt the protocol: stderr only.
console.log = (...args) => process.stderr.write(args.map(String).join(' ') + '\n');
console.warn = console.log;
console.error = console.log;

// GB Studio's code: a source tree (<src> <node_modules>) or a bundle (--bundle <file>).
const loader = require('./plugin_bridge_gbstudio.cjs');
const args = process.argv.slice(2);
const gb = args[0] === '--bundle' ? loader.fromBundle(args[1]) : loader.fromSources(args[0], args[1]);
const {ScriptBuilder, LATEST_PROJECT_VERSION, loadScriptEventHandlerFromUntrustedString, signatures} = gb;

// -- line protocol -----------------------------------------------------------
let pending = '';
const readMessage = () => {
  const buffer = Buffer.alloc(65536);
  for (;;) {
    const newline = pending.indexOf('\n');
    if (newline >= 0) {
      const line = pending.slice(0, newline);
      pending = pending.slice(newline + 1);
      return JSON.parse(line);
    }
    let n = 0;
    try { n = fs.readSync(0, buffer, 0, buffer.length, null); } catch (e) {
      if (e.code === 'EAGAIN') continue;
      if (e.code === 'EOF') process.exit(0);
      throw e;
    }
    if (n === 0) process.exit(0);
    pending += buffer.toString('utf8', 0, n);
  }
};
const send = (message) => fs.writeSync(1, JSON.stringify(message) + '\n');

const functions = new Map();
let nextFunction = 1;
const encode = (value) => {
  if (value === undefined) return {__undef__: 1};
  if (typeof value === 'function') {
    const id = nextFunction++;
    functions.set(id, value);
    return {__fn__: id};
  }
  if (value instanceof Map) return Object.fromEntries([...value].map(([k, v]) => [k, encode(v)]));
  if (Array.isArray(value)) return value.map(encode);
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = encode(v);
    return out;
  }
  return value;
};
const decode = (value) => {
  if (Array.isArray(value)) return value.map(decode);
  if (value && typeof value === 'object') {
    if (value.__undef__ === 1 && Object.keys(value).length === 1) return undefined;
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = decode(v);
    return out;
  }
  return value;
};

// -- builder state -------------------------------------------------------------
// ScriptBuilderBase fields that the V3 builder shares (same names, same shapes).
const STATE = ['output', 'dependencies', 'nextLabel', 'labelLookup', 'localsLookup', 'localsSize',
               'stackPtr', 'labelStackSize', 'headers', 'dataTables', 'actorIndex'];
let current = null;                     // the GB Studio ScriptBuilder of the running compile
let warnings = [];                      // options.warnings(msg) of GB Studio, handed to V3
const stateOf = (sb) => {
  if (!sb) return null;
  const out = {warnings};
  warnings = [];
  for (const k of STATE) out[k] = sb[k];
  return out;
};
const applyState = (sb, state) => {
  if (!sb || !state) return;
  // output is the array GB Studio's helpers hold: update it in place.
  sb.output.splice(0, sb.output.length, ...state.output);
  for (const k of STATE) if (k !== 'output') sb[k] = state[k];
};

let handlers = {};
let methods = {};                       // GB name or normalized name -> V3 method (from Python)
const normalize = (name) => {
  const lead = name.length - name.replace(/^_+/, '').length;
  return '_'.repeat(lead) + name.slice(lead).replace(/_/g, '').toLowerCase();
};
// Wait for the reply to our own request, serving Python's requests meanwhile.
const request = (message) => {
  const sb = current;
  send({...message, state: stateOf(sb)});
  for (;;) {
    const m = readMessage();
    if (m.op === 'reply') {
      current = sb;
      applyState(sb, m.state);
      if (m.error !== undefined) throw new Error(m.error);
      return decode(m.value);
    }
    serve(m);
    current = sb;
  }
};

const newBuilder = (options) => {
  const argLookup = options.argLookup || {actor: {}, variable: {}};
  const sb = new ScriptBuilder([], {
    ...options,
    argLookup: {actor: new Map(Object.entries(argLookup.actor || {})),
                variable: new Map(Object.entries(argLookup.variable || {}))},
    scriptEventHandlers: {},
    warnings: (message) => { warnings.push(String(message)); },
    compileEvents: (_builder, events) => { request({op: 'compileEvents', events: encode(events || [])}); },
  });
  for (const key of Object.keys(sb)) {
    const v3 = methods[key] || methods[normalize(key)];
    if (typeof sb[key] === 'function' && v3 && callableAs(key, v3)) {
      sb[key] = (...args) => request({op: 'call', name: v3.name, args: encode(args)});
    }
  }
  return sb;
};

// A V3 method replaces GB Studio's only if every positional call of GB Studio fits it
// (same arity; names differ between the ports). Otherwise GB Studio's own body runs,
// and its inner calls reach V3. Signatures read from the released TypeScript
// (plugin_bridge_gbstudio.cjs; total null = rest parameter).
const callableAs = (key, v3) => {
  const gb = signatures[key];
  if (!gb) return false;
  return v3.required <= gb.required && (v3.varargs || (gb.total !== null && v3.total >= gb.total));
};

const serve = (m) => {
  if (m.op === 'invoke') {
    const fn = functions.get(m.fn);
    applyState(current, m.state);
    let value;
    try {
      value = fn(...decode(m.args));
    } catch (e) {
      send({op: 'threw', message: String(e && e.message || e), state: stateOf(current)});
      return;
    }
    send({op: 'returned', value: encode(value), state: stateOf(current)});
  } else if (m.op === 'compile') {
    const handler = handlers[m.id];
    const outer = current;
    const sb = current = newBuilder(decode(m.options));
    applyState(sb, m.state);
    sb.eventCommand = m.id;
    try {
      handler.compile(decode(m.input), {
        LATEST_PROJECT_VERSION, ...sb.options, ...sb, scriptSymbolName: sb.options.scriptSymbolName,
        event: decode(m.event),
      });
      send({op: 'done', state: stateOf(sb)});
    } catch (e) {
      send({op: 'failed', message: String(e && e.message || e), state: stateOf(sb)});
    }
    current = outer;
  } else if (m.op === 'oracle') {
    // Tests: the same events compiled by GB Studio alone (released ScriptBuilder,
    // compileEntityEvents steps), plugin events only.
    const output = [];
    const options = decode(m.options);
    const compileEvents = (sb, events = []) => {
      for (const event of events) {
        if (event.args && event.args.__comment) continue;
        sb.eventCommand = event.command;
        handlers[event.command].compile({...event.args, ...event.children}, {
          LATEST_PROJECT_VERSION, ...sb.options, ...sb, scriptSymbolName: sb.options.scriptSymbolName, event});
      }
    };
    const oracleWarnings = [];
    try {
      const sb = new ScriptBuilder(output, {...options, scriptEventHandlers: handlers, compileEvents,
                                            warnings: (w) => oracleWarnings.push(String(w))});
      compileEvents(sb, decode(m.events));
      sb._packLocals();
      sb.scriptEnd();
      send({op: 'oracle', text: sb.toScriptString(options.scriptSymbolName, !!m.lock), warnings: oracleWarnings});
    } catch (e) {
      send({op: 'oracle', error: String(e && e.message || e)});
    }
  } else if (m.op === 'quit') {
    process.exit(0);
  } else {
    throw new Error(`plugin bridge: unexpected message ${m.op}`);
  }
};

(async () => {
  const first = readMessage();
  if (first.op !== 'load') throw new Error('plugin bridge: expected load');
  methods = first.methods;
  const events = [];
  for (const file of first.files) {
    // GB Studio's file reader for a plugin: files of its own folder only.
    const dir = path.dirname(path.resolve(file));
    const reader = (name, encoding) => {
      const target = path.resolve(dir, name);
      if (!target.startsWith(dir + path.sep)) throw new Error(`${name}: outside the plugin folder`);
      return fs.readFileSync(target, encoding);
    };
    try {
      const handler = await loadScriptEventHandlerFromUntrustedString(fs.readFileSync(file, 'utf8'), file, reader);
      handlers[handler.id] = handler;
      events.push({file, id: handler.id, name: handler.name || null, fields: encode(handler.fields || []),
                   sceneTypes: handler.sceneTypes || null,
                   isConditional: !!handler.isConditional,
                   waitUntilAfterInitFade: !!handler.waitUntilAfterInitFade,
                   allowChildrenBeforeInitFade: !!handler.allowChildrenBeforeInitFade,
                   editableSymbol: !!handler.editableSymbol});
    } catch (e) {
      events.push({file, error: String(e && e.message || e)});
    }
  }
  send({op: 'loaded', events});
  for (;;) serve(readMessage());
})().catch((e) => {
  process.stderr.write(String(e && e.stack || e) + '\n');
  process.exit(1);
});
