// Replays journal operations on ngpcraft/runtime/save_journal.c (wasm), see tests/test_save_journal_c.py.
const fs = require("fs");
const crypto = require("crypto");
const wasm = fs.readFileSync(process.argv[2]);
const ops = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const sha = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");

WebAssembly.instantiate(wasm, {}).then(({ instance }) => {
  const x = instance.exports;
  const mem = () => new Uint8Array(x.memory.buffer);
  const flash = x.flash.value, input = x.input.value;
  const out = [];
  x.harness_reset();
  for (const op of ops) {
    if (op.op === "reset") { x.harness_reset(); continue; }
    if (op.op === "snapshot") { x.harness_snapshot(); continue; }
    if (op.op === "restore") { x.harness_restore(); continue; }
    if (op.op === "get") {
      // `input` still holds the last saved data: compare ngc_save_get with it.
      if (Number(x.harness_find(op.slot)) < 0) { out.push({ get: -1 }); continue; }
      out.push({ get: x.harness_get_matches(op.length) });
      continue;
    }
    x.harness_cut(op.cut, op.erase);
    let ret;
    if (op.op === "save") {
      const data = Buffer.from(op.data, "hex");
      mem().set(data, input);
      ret = x.harness_save(op.slot, data.length, op.mode || 0);
    } else {
      ret = x.harness_clear(op.slot);
    }
    x.harness_power_on();
    const find = [];
    for (let s = 0; s < 3; s++) {
      const at = Number(x.harness_find(s));
      if (at < 0) { find.push(null); continue; }
      const length = new Uint16Array(x.memory.buffer, x.found_length.value, 1)[0];
      find.push(sha(mem().slice(flash + at, flash + at + length)));
    }
    out.push({ ret, flash: sha(mem().slice(flash, flash + 16384)), find });
  }
  process.stdout.write(JSON.stringify(out));
});
