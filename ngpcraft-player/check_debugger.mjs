// NgpCraft Studio: check the Play window debugger path on a V3 debug build,
// with the built WebAssembly player (no browser):
//   node ngpcraft-player/check_debugger.mjs <V3 export folder> [frames]
// The export folder holds export.json and rom/ (python -m ngpcraft export
// --debug, then build-rom). Checks, for every VM_STEP stop of the run:
// the context read at XSP+4 is one of CTXS, its PC lies in a script; and that
// a frame stopped by a breakpoint resumes to the same frame count as an
// unbroken run (same instructions, same picture).
import fs from 'fs';
import path from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.join(here, '..', 'appData', 'wasm', 'ngpcraft-web');
const exportDir = process.argv[2];
const frames = Number(process.argv[3] || 120);
if (!exportDir) throw new Error('usage: check_debugger.mjs <V3 export folder> [frames]');
const manifest = JSON.parse(fs.readFileSync(path.join(exportDir, 'export.json'), 'utf8'));
const report = JSON.parse(fs.readFileSync(path.join(exportDir, 'rom', 'build-report.json'), 'utf8'));
if (!manifest.debug || !report.debug) throw new Error('not a debug build (export --debug)');
const rom = fs.readFileSync(path.join(exportDir, 'rom', 'game.ngc'));
const layout = report.debug;
const scripts = Object.entries(manifest.scripts);
const starts = new Map(manifest.debug.labels.filter((l) => l.symbol.startsWith('GBVM$'))
  .map((l) => [l.address, l.symbol]));

const { default: createNgpCraft } = await import(pathToFileURL(path.join(web, 'ngpcraft.mjs')).href);

const boot = async () => {
  const m = await createNgpCraft({
    locateFile: (f) => path.join(web, f),
    wasmBinary: fs.readFileSync(path.join(web, 'ngpcraft.wasm')),
  });
  const p = m._malloc(rom.length);
  m.HEAPU8.set(rom, p);
  if (!m._web_load(p, rom.length)) throw new Error('ROM refused');
  return m;
};
const readPtr = (m, addr) => {
  const p = m._malloc(4);
  m._web_read_mem(addr, p, 4);
  const v = (m.HEAPU8[p] | (m.HEAPU8[p + 1] << 8) | (m.HEAPU8[p + 2] << 16)) >>> 0;
  m._free(p);
  return v;
};
const readByte = (m, addr) => {
  const p = m._malloc(1);
  m._web_read_mem(addr, p, 1);
  const v = m.HEAPU8[p];
  m._free(p);
  return v;
};
const picture = (m) => Buffer.from(m.HEAPU8.subarray(m._web_video(), m._web_video() + 160 * 152 * 4));

// Unbroken reference run.
const plain = await boot();
for (let f = 0; f < frames; f++) plain._web_run(0);
const reference = picture(plain);

const m = await boot();
const bp = m._malloc(4);
new DataView(m.HEAPU8.buffer).setUint32(bp, layout.VM_STEP >>> 0, true);
m._web_set_breakpoints(bp, 1);
let stops = 0;
const hit = new Set();
const banks = new Set();
for (let f = 0; f < frames; f++) {
  let status = m._web_run(0);
  while (status === 40) {
    stops++;
    const ctx = readPtr(m, (m._web_cpu_reg(7) >>> 0) + manifest.debug.step_context.offset);
    if (ctx < layout.CTXS || (ctx - layout.CTXS) % layout.sizeof) {
      throw new Error(`stop ${stops}: context 0x${ctx.toString(16)} is not in CTXS`);
    }
    // As studio.js: label address = bank * 0x10000 + offset in the bank.
    const bank = readByte(m, ctx + layout.fields.bank);
    const bankAddresses = layout.bank_addresses || [layout.bank_0_address];
    const offset = readPtr(m, ctx + layout.fields.PC) - bankAddresses[bank];
    if (!scripts.some(([, s]) => (s.bank || 0) === bank && s.address <= offset && offset < s.address + s.bytes)) {
      throw new Error(`stop ${stops}: PC bank ${bank} offset ${offset} is in no script`);
    }
    banks.add(bank);
    const address = bank * 0x10000 + offset;
    if (starts.has(address)) hit.add(starts.get(address));
    status = m._web_run(0);
  }
  if (![0, 1, 41].includes(status)) throw new Error(`frame ${f}: status ${status}`);
}
const same = picture(m).equals(reference);
console.log(JSON.stringify({ frames, vm_steps: stops, banks_seen: [...banks], events_started: hit.size,
  events: [...hit].slice(0, 6), picture_equal_to_unbroken_run: same }, null, 1));
if (!stops || !hit.size || !same) process.exit(1);
