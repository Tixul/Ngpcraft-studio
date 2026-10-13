// NgpCraft Studio: Play window script. Loads the built ROM, starts it, and
// with ?debug=true talks to GB Studio's debugger panel through the same
// messages as its binjgb client (appData/webTemplates/binjgb/js/debugger.js):
// "initialized" -> "listener-ready", then "update-globals" with the values of
// the global variables, the script contexts and the current scene.
// A V3 debug build (export --debug, _Engine/_v3/docs/M7_DEBUG.md) gives the
// GBVM$ event symbols (bank addresses), the console address of the script
// bank, the SCRIPT_CTX layout cc900 linked and the address of VM_STEP. As in
// binjgb, a CPU breakpoint on VM_STEP sees every VM instruction: it is armed
// only while something needs it (event breakpoints, step, pause on script
// change); the contexts are read from RAM at any time.
import './player.js';

const params = new URLSearchParams(location.search);
const player = document.querySelector('ngpcraft-player');
const SCRIPT_MEMORY_SYMBOL = '_script_memory';
const CURRENT_SCENE_SYMBOL = '_ngc_scene';
const MAX_GLOBAL_VARS = 'MAX_GLOBAL_VARS';
const UPDATE_MS = 100;
const XSP = 7;

const start = async () => {
  const rom = params.get('rom');
  if (!rom) return;
  try {
    await player.loadURL(rom);
    await player.play();
  } catch (e) {
    player.report(e.message);
  }
};

// binjgb debugger.js parseDebuggerSymbol.
const parseDebuggerSymbol = (input) => {
  const match = input.match(/GBVM\$([^$]+)\$([^$]+)\$([^$]+)\$([^$]+)\$([^$]+)\$([^$]+)/);
  if (!match) return undefined;
  return {
    scriptSymbol: match[1],
    scriptEventId: match[2].replace(/_/g, '-'),
    sceneId: match[3].replace(/_/g, '-'),
    entityType: match[4],
    entityId: match[5].replace(/_/g, '-'),
    scriptKey: match[6],
  };
};

const toAddrHex = (value) => ('000000' + value.toString(16).toUpperCase()).slice(-6);

class StudioDebugger {
  constructor(api) {
    this.api = api;
    this.ready = false;
    this.memoryMap = {};
    this.globalVariables = {};
    this.variableMap = {};
    this.watchedVariables = [];
    this.pauseOnWatchedVariableChanged = true;
    this.pauseOnScriptChanged = false;
    this.pauseOnVMStep = false;
    this.breakpoints = [];
    this.symbols = [];
    this.sceneSymbols = [];
    this.prevGlobals = null;
    this.buffer = 0;
    this.debug = null;
    this.labelAddrs = [];
    this.labelSymbols = new Map();
    this.scriptContexts = [];
    this.armed = false;
    this.stopped = false;
  }

  get module() {
    return player.module;
  }

  scratch(n) {
    if (!this.buffer) this.buffer = this.module._malloc(512);
    return n <= 512 ? this.buffer : 0;
  }

  readBytes(addr, n) {
    const p = this.scratch(n);
    if (!p || !this.module._web_read_mem(addr, p, n)) return null;
    return this.module.HEAPU8.subarray(p, p + n);
  }

  readInt16(addr) {
    const b = this.readBytes(addr, 2);
    if (!b) return 0;
    const v = b[0] | (b[1] << 8);
    return v >= 0x8000 ? v - 0x10000 : v;
  }

  readByte(addr) {
    const b = this.readBytes(addr, 1);
    return b ? b[0] : 0;
  }

  // TLCS-900 pointers: 32-bit little-endian, 24-bit address space.
  readPtr(addr) {
    const b = this.readBytes(addr, 4);
    return b ? (b[0] | (b[1] << 8) | (b[2] << 16)) >>> 0 : 0;
  }

  currentScene() {
    const addr = this.memoryMap[CURRENT_SCENE_SYMBOL];
    if (addr === undefined || !this.module) return '';
    return this.sceneSymbols[this.readByte(addr)] || '';
  }

  writeInt16(addr, value) {
    const p = this.scratch(2);
    if (!p) return;
    this.module.HEAPU8[p] = value & 0xff;
    this.module.HEAPU8[p + 1] = (value >> 8) & 0xff;
    this.module._web_write_mem(addr, p, 2);
  }

  initialize(data) {
    this.memoryMap = data.memoryMap || {};
    this.globalVariables = data.globalVariables || {};
    this.variableMap = data.variableMap || {};
    this.watchedVariables = data.watchedVariables || [];
    this.pauseOnWatchedVariableChanged = !!data.pauseOnWatchedVariableChanged;
    this.pauseOnScriptChanged = !!data.pauseOnScriptChanged;
    this.breakpoints = data.breakpoints || [];
    this.sceneSymbols = data.ngpcSceneSymbols || [];
    // Panel order: by V3 variable index (the panel reads values by position).
    this.symbols = Object.keys(this.globalVariables)
      .filter((s) => s !== MAX_GLOBAL_VARS)
      .sort((a, b) => this.globalVariables[a] - this.globalVariables[b]);
    this.ready = this.memoryMap[SCRIPT_MEMORY_SYMBOL] !== undefined;
    this.prevGlobals = null;
    this.initializeScripts(data.ngpcDebug);
    this.arm();
  }

  // binjgb memoryDict: per address, the last GBVM$ symbol, else a GBVM_END$.
  initializeScripts(debug) {
    this.debug = debug && debug.labels && debug.layout && debug.stepContext ? debug : null;
    this.labelSymbols = new Map();
    this.labelAddrs = [];
    if (!this.debug) return;
    for (const { symbol, address } of this.debug.labels) {
      if (symbol.startsWith('GBVM$')) this.labelSymbols.set(address, symbol);
      else if (!this.labelSymbols.has(address)) this.labelSymbols.set(address, symbol);
    }
    this.labelAddrs = Array.from(this.labelSymbols.keys()).sort((a, b) => a - b);
  }

  closestAddress(address) {
    let lo = 0;
    let hi = this.labelAddrs.length - 1;
    let found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (this.labelAddrs[mid] <= address) {
        found = this.labelAddrs[mid];
        lo = mid + 1;
      } else hi = mid - 1;
    }
    return found;
  }

  // The VM_STEP breakpoint, only while something needs it.
  arm() {
    if (!this.debug || !this.module) return;
    const want = this.breakpoints.length > 0 || this.pauseOnScriptChanged || this.pauseOnVMStep;
    if (want === this.armed) return;
    this.armed = want;
    const p = this.scratch(4);
    const step = this.debug.layout.VM_STEP >>> 0;
    this.module.HEAPU8.set([step & 0xff, (step >> 8) & 0xff, (step >> 16) & 0xff, step >>> 24], p);
    this.module._web_set_breakpoints(p, want ? 1 : 0);
  }

  // The contexts as binjgb lists them; `current` is the context being
  // stepped (on VM_STEP: its argument at XSP+4), else executing_ctx.
  contexts(currentCtx) {
    if (!this.debug) return [];
    const { layout } = this.debug;
    const f = layout.fields;
    const prev = this.scriptContexts;
    const out = [];
    let ctx = this.readPtr(layout.first_ctx);
    while (ctx && out.length < 64) {
      const pc = this.readPtr(ctx + f.PC);
      // Labels are bank * 0x10000 + offset in the bank (ngpcraft-v3
      // debug_build.py); one bank: bank_0_address only (older builds).
      const bank = this.readByte(ctx + f.bank);
      const bankAddresses = layout.bank_addresses || [layout.bank_0_address];
      const address = bank < bankAddresses.length
        ? bank * 0x10000 + (pc - bankAddresses[bank])
        : -1;
      const closestAddr = this.closestAddress(address);
      const closestSymbol = closestAddr >= 0 ? this.labelSymbols.get(closestAddr) : '';
      const stackPtr = this.readPtr(ctx + f.stack_ptr);
      const stackBase = this.readPtr(ctx + f.base_addr);
      let stackString = '';
      for (let i = stackBase; i < stackPtr + 4 && i - stackBase < 256; i += 2) {
        stackString += `${i === stackPtr ? '->' : '  '}${toAddrHex(i)}: ${this.readInt16(i)}\n`;
      }
      const prevCtx = prev[out.length];
      out.push({
        address,
        bank,
        current: ctx === currentCtx,
        closestAddr,
        closestSymbol,
        closestGBVMSymbol: parseDebuggerSymbol(closestSymbol),
        prevClosestSymbol: prevCtx?.closestSymbol,
        prevClosestGBVMSymbol: prevCtx?.closestGBVMSymbol,
        stackString,
      });
      ctx = this.readPtr(ctx + f.next);
    }
    return out;
  }

  // player.js, on the VM_STEP breakpoint: false pauses there (binjgb rules).
  onBreak() {
    const sp = this.module._web_cpu_reg(XSP) >>> 0;
    const current = this.readPtr(sp + this.debug.stepContext.offset);
    this.scriptContexts = this.contexts(current);
    const ctx = this.scriptContexts.find((c) => c.current);
    if (!ctx || !ctx.closestGBVMSymbol) return true;
    const changed = ctx.closestSymbol !== ctx.prevClosestSymbol;
    if (this.pauseOnVMStep && ctx.closestGBVMSymbol.scriptEventId !== 'end' && changed) {
      this.pauseOnVMStep = false;
      return this.stop();
    }
    if (ctx.address === ctx.closestAddr && changed &&
        this.breakpoints.includes(ctx.closestGBVMSymbol.scriptEventId)) {
      return this.stop();
    }
    if (this.pauseOnScriptChanged && (!ctx.prevClosestGBVMSymbol ||
        ctx.closestGBVMSymbol.scriptSymbol !== ctx.prevClosestGBVMSymbol.scriptSymbol)) {
      return this.stop();
    }
    return true;
  }

  // Paused on a breakpoint: the contexts just read stay those shown.
  stop() {
    this.stopped = true;
    setTimeout(() => {
      this.arm();
      this.send();
    }, 0);
    return false;
  }

  addressOf(symbol) {
    const index = this.globalVariables[symbol];
    if (index === undefined) return undefined;
    return this.memoryMap[SCRIPT_MEMORY_SYMBOL] + index * 2;
  }

  globals() {
    if (!this.ready || !this.module) return [];
    return this.symbols.map((symbol) => this.readInt16(this.addressOf(symbol)));
  }

  setGlobal(symbol, value) {
    const addr = this.addressOf(symbol);
    if (addr === undefined || !this.module) return;
    this.writeInt16(addr, value);
    this.prevGlobals = this.globals();
    this.send();
  }

  // After each frame: pause when a watched variable changed.
  onFrame() {
    if (!this.ready || !this.pauseOnWatchedVariableChanged || !this.watchedVariables.length) return;
    const globals = this.globals();
    const prev = this.prevGlobals;
    this.prevGlobals = globals;
    if (!prev) return;
    const changed = this.watchedVariables.some((variableId) => {
      const symbol = this.variableMap[variableId]?.symbol;
      const k = this.symbols.indexOf(symbol);
      return k >= 0 && prev[k] !== globals[k];
    });
    if (changed) {
      player.pause();
      this.send();
    }
  }

  send() {
    // Armed, the VM_STEP breakpoint keeps the contexts (and the previous
    // symbols its rules compare); a read here would hide an event change.
    if (this.debug && !this.armed && !this.stopped) {
      this.scriptContexts = this.contexts(this.readPtr(this.debug.layout.executing_ctx));
    }
    const current = this.scriptContexts.find((c) => c.current);
    this.api.debugger.sendToProjectWindow({
      action: 'update-globals',
      data: this.globals(),
      vram: '',
      background: '',
      isPaused: !player.running,
      scriptContexts: this.scriptContexts,
      currentSceneSymbol: this.currentScene(),
      currentScriptSymbol: current?.closestGBVMSymbol?.scriptSymbol ?? '',
      currentScriptAddr: current?.closestAddr ?? 0,
      currentScriptPCAddr: current?.address ?? 0,
    });
  }

  resume() {
    this.stopped = false;
    this.arm();
    player.play();
  }

  onMessage(packet) {
    switch (packet.action) {
      case 'listener-ready':
        this.initialize(packet.data);
        this.send();
        break;
      case 'pause':
        player.pause();
        this.send();
        break;
      case 'resume':
        this.pauseOnVMStep = false;
        this.resume();
        break;
      case 'step':
        if (this.debug) {
          // binjgb step: run until the current context reaches another event.
          this.pauseOnVMStep = true;
          this.resume();
        } else {
          player.stepFrame();
          this.send();
        }
        break;
      case 'step-frame':
        this.stopped = false;
        player.stepFrame();
        this.send();
        break;
      case 'set-breakpoints':
        this.breakpoints = packet.data || [];
        this.arm();
        break;
      case 'pause-on-script':
        this.pauseOnScriptChanged = !!packet.data;
        this.arm();
        break;
      case 'set-global':
        this.setGlobal(packet.data.symbol, packet.data.value);
        break;
      case 'set-watched':
        this.watchedVariables = packet.data || [];
        break;
      case 'pause-on-var':
        this.pauseOnWatchedVariableChanged = !!packet.data;
        break;
      default:
        break;
    }
  }

  connect() {
    this.api.events.debugger.data.subscribe((_, packet) => this.onMessage(packet));
    player.onFrame = () => this.onFrame();
    player.onBreak = () => (this.debug ? this.onBreak() : true);
    setInterval(() => {
      if (this.ready) this.send();
    }, UPDATE_MS);
    this.api.debugger.sendToProjectWindow({ action: 'initialized' });
  }
}

player.addEventListener('ngpc-ready', () => {
  if (params.get('debug') === 'true' && window.API && !window.studioDebugger) {
    window.studioDebugger = new StudioDebugger(window.API);
    window.studioDebugger.connect();
  }
}, { once: true });

start();
