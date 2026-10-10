/*
 * NgpCraft: lib/helpers/fsCopy resolves once the copies are written. GB
 * Studio 4.3.2 resolved at the end of the reading, before the output was
 * flushed; createProject then read the template's project.gbsproj back and
 * could get it truncated ("Unexpected end of JSON input" on a new project).
 * The writes are slowed down here so that the order is deterministic.
 */
import Path from "path";
import os from "os";
import { Writable } from "stream";
import {
  appendFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  writeFileSync,
} from "fs";
import fs from "fs-extra";
import copy from "lib/helpers/fsCopy";

afterEach(() => {
  jest.restoreAllMocks();
});

test("a copied file is complete as soon as the copy resolves", async () => {
  const root = mkdtempSync(Path.join(os.tmpdir(), "ngpcraft-copy-"));
  const src = Path.join(root, "src");
  mkdirSync(src);
  // Small like a project.gbsproj: under the write buffer, no backpressure
  // pauses the reading, so its end comes before the write is done.
  const data = Buffer.alloc(2048, 7);
  writeFileSync(Path.join(src, "project.gbsproj"), data);
  const spy = jest
    .spyOn(fs, "createWriteStream")
    .mockImplementation((dest: unknown) => {
      writeFileSync(String(dest), "");
      return new Writable({
        write(chunk, _encoding, callback) {
          setTimeout(() => {
            appendFileSync(String(dest), chunk);
            callback();
          }, 5);
        },
      }) as unknown as fs.WriteStream;
    });
  const dest = Path.join(root, "dest");
  await copy(src, dest);
  expect(spy).toHaveBeenCalledTimes(1);
  expect(readFileSync(Path.join(dest, "project.gbsproj")).length).toBe(
    data.length,
  );
});
