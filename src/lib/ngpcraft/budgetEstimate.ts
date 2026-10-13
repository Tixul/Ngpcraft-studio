/*
 * NgpCraft: live budget of a scene (V3 `budget`, scene_budget.py), read from
 * the saved project like the palette plan: tiles of the background(s), the
 * sprite sheets (pooled as the build does), the text reserve. Unlike the
 * build, an over-budget scene still gets its numbers.
 */
import Path from "path";
import { spawn } from "child_process";
import { getPython, getV3Root, v3Env } from "lib/ngpcraft/v3Paths";
import type { NgpcBudgetEstimate } from "shared/lib/ngpcraft/sceneBudget";

interface V3SceneEstimate {
  scene_id: string;
  used_tiles: number;
  background_tiles?: number;
  second_background_tiles?: number;
  sprite_tiles: number;
  text_reserve: number;
  background_palettes?: number[];
  over: boolean;
  sheets: { name: string; tiles: number }[];
  errors: string[];
}

export const readBudgetEstimate = async (
  projectPath: string,
  sceneId: string,
): Promise<NgpcBudgetEstimate | { error: string }> => {
  const v3Root = await getV3Root();
  const result = await new Promise<{ code: number; out: string; err: string }>(
    (resolve) => {
      let out = "";
      let err = "";
      const child = spawn(
        getPython(),
        [
          "-B",
          "-m",
          "ngpcraft",
          "budget",
          Path.dirname(projectPath),
          "--scene",
          sceneId,
        ],
        { cwd: v3Root, env: v3Env() },
      );
      child.stdout.on("data", (d) => {
        out += d;
      });
      child.stderr.on("data", (d) => {
        err += d;
      });
      child.on("error", (e) => resolve({ code: -1, out, err: String(e) }));
      child.on("close", (code) => resolve({ code: code ?? -1, out, err }));
    },
  );
  if (result.code !== 0) {
    return { error: result.err.trim().split("\n").pop() || "budget failed" };
  }
  try {
    const scene: V3SceneEstimate = JSON.parse(result.out).scenes[0];
    return {
      sceneId: scene.scene_id,
      usedTiles: scene.used_tiles,
      backgroundTiles: scene.background_tiles ?? 0,
      secondBackgroundTiles: scene.second_background_tiles ?? 0,
      spriteTiles: scene.sprite_tiles,
      textReserve: scene.text_reserve,
      backgroundPalettes: Math.max(0, ...(scene.background_palettes ?? [])),
      over: scene.over,
      sheets: scene.sheets,
      errors: scene.errors,
    };
  } catch (e) {
    return { error: `budget output is not JSON: ${e}` };
  }
};
