/*
 * NgpCraft: live budget of a scene (V3 `budget`) read by the editor from the
 * saved project. Needs the V3 compiler and Python; skipped otherwise.
 */
import Path from "path";
import os from "os";
import { existsSync, mkdtempSync } from "fs";
import createProject from "lib/project/createProject";
import loadProject from "lib/project/loadProjectData";
import { readBudgetEstimate } from "lib/ngpcraft/budgetEstimate";
import { ngpcraftDefaultV3Root } from "consts";

const v3Root = process.env.NGPCRAFT_V3_HOME || ngpcraftDefaultV3Root;
const available = existsSync(Path.join(v3Root, "ngpcraft"));

(available ? test : test.skip)(
  "a gbs2 scene gets its tiles, sheets and text reserve",
  async () => {
    const tmp = mkdtempSync(Path.join(os.tmpdir(), "ngpcraft-budget-"));
    const projectFile = await createProject({
      name: "Demo",
      template: "gbs2",
      path: tmp,
    });
    const loaded = await loadProject(projectFile);
    const scene = loaded.resources.scenes[0];
    const estimate = await readBudgetEstimate(projectFile, scene.id);
    if ("error" in estimate) {
      throw new Error(estimate.error);
    }
    expect(estimate.sceneId).toBe(scene.id);
    expect(estimate.backgroundTiles).toBeGreaterThan(0);
    expect(estimate.spriteTiles).toBeGreaterThan(0);
    expect(estimate.usedTiles).toBe(
      estimate.backgroundTiles +
        estimate.secondBackgroundTiles +
        estimate.spriteTiles +
        estimate.textReserve,
    );
    expect(estimate.sheets.length).toBeGreaterThan(0);
  },
  300000,
);
