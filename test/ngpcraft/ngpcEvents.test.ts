/*
 * NgpCraft: the NGPC events of the editor (src/lib/events/eventNgpc*.js) are
 * the ones the V3 compiles natively (ngpcraft-v3/ngpcraft/parity NGPC_EVENTS),
 * and every text they show exists in English and French.
 */
import Path from "path";
import { readdirSync, readFileSync } from "fs";
import { NGPC_EVENTS } from "shared/lib/ngpcraft/catalogue";

const root = Path.join(__dirname, "../..");
const eventsDir = Path.join(root, "src/lib/events");
const files = readdirSync(eventsDir).filter((f) =>
  /^eventNgpc\w*\.js$/.test(f),
);
const source = (file: string) =>
  readFileSync(Path.join(eventsDir, file), "utf8");
const lang = (name: string): Record<string, string> =>
  JSON.parse(readFileSync(Path.join(root, "src/lang", `${name}.json`), "utf8"));

describe("NGPC events", () => {
  test("one editor file per NGPC event", () => {
    const ids = files.map((file) => {
      const match = source(file).match(/const id = "(EVENT_NGPC_\w+)";/);
      expect(match).not.toBeNull();
      return match ? match[1] : "";
    });
    expect(new Set(ids)).toEqual(new Set(NGPC_EVENTS));
  });

  test("same list as the V3", () => {
    const parity = readFileSync(
      Path.join(root, "ngpcraft-v3/ngpcraft/parity/__init__.py"),
      "utf8",
    );
    const block = parity.match(/NGPC_EVENTS = frozenset\(\{([^}]*)\}\)/);
    expect(block).not.toBeNull();
    const v3 = new Set((block ? block[1] : "").match(/EVENT_NGPC_\w+/g));
    expect(v3).toEqual(new Set(NGPC_EVENTS));
  });

  test("texts in English and French", () => {
    const en = lang("en");
    const fr = lang("fr");
    for (const file of files) {
      const id = (source(file).match(/const id = "(\w+)";/) || [])[1];
      const keys = [
        id,
        ...Array.from(source(file).matchAll(/l10n\("(\w+)"\)/g), (m) => m[1]),
        ...Array.from(
          source(file).matchAll(/"(FIELD_NGPC_\w+)"/g),
          (m) => m[1],
        ),
        ...Array.from(
          source(file).matchAll(/"(EVENT_GROUP_\w+)"/g),
          (m) => m[1],
        ),
      ];
      for (const key of keys) {
        expect([file, key, key in en]).toEqual([file, key, true]);
        expect([file, key, key in fr]).toEqual([file, key, true]);
      }
    }
  });
});
