/* eslint-disable camelcase */ // Keys of V3 / GB Studio file formats.
/*
 * NgpCraft F7: ngpcraft.json settings. Defaults are removed from the file,
 * unknown keys survive, an empty extension never creates a file.
 */
import Path from "path";
import os from "os";
import { mkdtempSync, readFileSync, writeFileSync, existsSync } from "fs";
import {
  getNgpcProjectSettings,
  hasNgpcContent,
  setNgpcProjectSetting,
} from "shared/lib/ngpcraft/projectExtension";
import {
  readNgpcExtension,
  writeNgpcExtension,
} from "lib/ngpcraft/projectExtension";

test("settings are set, read and removed without touching other keys", () => {
  const original = {
    schema: "ngpcraft-project/1",
    ngpc: { native_sfx: { "beep:1:2": [] } },
    resources: { "scene-1": { second_background: { background_id: "b" } } },
  };
  const withRate = setNgpcProjectSetting(original, "frame_rate", 30);
  expect(getNgpcProjectSettings(withRate).frame_rate).toBe(30);
  expect(withRate.resources).toEqual(original.resources);
  expect((withRate.ngpc as Record<string, unknown>).native_sfx).toEqual(
    original.ngpc.native_sfx,
  );
  // The input is not mutated.
  expect(original.ngpc).toEqual({ native_sfx: { "beep:1:2": [] } });

  const back = setNgpcProjectSetting(withRate, "frame_rate", undefined);
  expect(getNgpcProjectSettings(back).frame_rate).toBeUndefined();
  expect(back).toEqual(original);
});

test("a new extension starts with the schema", () => {
  const created = setNgpcProjectSetting(null, "actor_capacity", 25);
  expect(created).toEqual({
    schema: "ngpcraft-project/1",
    ngpc: { actor_capacity: 25 },
  });
  expect(hasNgpcContent(created)).toBe(true);
  expect(
    hasNgpcContent(setNgpcProjectSetting(created, "actor_capacity", undefined)),
  ).toBe(false);
});

test("the file is written only when it carries something", async () => {
  const root = mkdtempSync(Path.join(os.tmpdir(), "ngpcraft-f7-"));
  const path = Path.join(root, "ngpcraft.json");

  await writeNgpcExtension(root, { schema: "ngpcraft-project/1", ngpc: {} });
  expect(existsSync(path)).toBe(false);

  const extension = setNgpcProjectSetting(null, "frame_rate", 30);
  await writeNgpcExtension(root, extension);
  expect(await readNgpcExtension(root)).toEqual(extension);

  // An existing file is kept up to date, even when it becomes empty.
  await writeNgpcExtension(
    root,
    setNgpcProjectSetting(extension, "frame_rate", undefined),
  );
  expect(JSON.parse(readFileSync(path, "utf8")).ngpc).toEqual({});
});

test("an unreadable file is an error, not an empty extension", async () => {
  const root = mkdtempSync(Path.join(os.tmpdir(), "ngpcraft-f7-"));
  writeFileSync(Path.join(root, "ngpcraft.json"), "{ not json");
  await expect(readNgpcExtension(root)).rejects.toThrow();
});
