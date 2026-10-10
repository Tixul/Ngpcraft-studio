const fs = require('fs');
const [wasm, casesPath] = process.argv.slice(2);
(async () => {
  const { instance } = await WebAssembly.instantiate(fs.readFileSync(wasm));
  const e = instance.exports;
  const results = [];
  for (const test of JSON.parse(fs.readFileSync(casesPath, 'utf8'))) {
    e.setup(test.seed);
    const initial = e.emit_snapshot();
    const before = Buffer.from(new Uint8Array(e.memory.buffer, e.blob_address(), e.blob_length())).toString('hex');
    if (test.badIdentity) e.corrupt_identity();
    if (test.badPointer) e.corrupt_code_pointer();
    if (test.badIdentity || test.badPointer) {
      results.push({ initial, ret: e.emit_snapshot(), length: e.blob_length() });
      continue;
    }
    if (test.data !== undefined) {
      const data = Buffer.from(test.data, 'hex');
      new Uint8Array(e.memory.buffer, e.blob_address(), data.length).set(data);
      const ret = e.restore_snapshot(data.length);
      const emitted = e.emit_snapshot();
      const after = Buffer.from(new Uint8Array(e.memory.buffer, e.blob_address(), e.blob_length())).toString('hex');
      results.push({ initial, before, ret, emitted, after, address: e.memory_address() });
    } else results.push({ initial, before, address: e.memory_address() });
  }
  process.stdout.write(JSON.stringify(results));
})().catch(e => { process.stderr.write(String(e)); process.exitCode = 1; });
