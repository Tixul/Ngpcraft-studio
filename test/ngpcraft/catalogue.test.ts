/*
 * NgpCraft F5: the editor reads the V3 event catalogue. Skipped without V3.
 */
import Path from "path";
import { existsSync } from "fs";
import { loadNgpcCatalogue } from "lib/ngpcraft/catalogue";
import { ngpcraftDefaultV3Root } from "consts";
import {
  isNgpcEventBuildable,
  ngpcEventDelivery,
  ngpcEventNote,
} from "shared/lib/ngpcraft/catalogue";

const available = existsSync(
  Path.join(
    process.env.NGPCRAFT_V3_HOME || ngpcraftDefaultV3Root,
    "ngpcraft",
    "parity",
    "gbstudio.json",
  ),
);

(available ? describe : describe.skip)("NgpCraft V3 catalogue", () => {
  test("lists the GB Studio events with their NGPC status", async () => {
    const catalogue = await loadNgpcCatalogue();
    expect(catalogue).toBeDefined();
    if (!catalogue) {
      return;
    }
    // Every GB Studio 4.3.2 event is listed (151 in the published release).
    expect(Object.keys(catalogue.events).length).toBe(151);

    // A Game Boy only event is refused, with the title of its gap.
    expect(ngpcEventDelivery(catalogue, "EVENT_PRINT")).toBe("refused");
    expect(isNgpcEventBuildable(catalogue, "EVENT_PRINT")).toBe(false);
    expect(catalogue.events.EVENT_PRINT.ecartTitle).toBeTruthy();
    expect(ngpcEventNote(catalogue, "EVENT_PRINT")).toContain("§12");

    // Common events are buildable.
    expect(isNgpcEventBuildable(catalogue, "EVENT_TEXT")).toBe(true);
    expect(isNgpcEventBuildable(catalogue, "EVENT_SWITCH_SCENE")).toBe(true);

    // An event from a plugin is not in the catalogue: the V3 compiles it
    // through the plugin loader (refused at build if it cannot).
    expect(ngpcEventDelivery(catalogue, "EVENT_MY_PLUGIN_THING")).toBe(
      "plugin",
    );
    expect(isNgpcEventBuildable(catalogue, "EVENT_MY_PLUGIN_THING")).toBe(
      true,
    );
  });
});
