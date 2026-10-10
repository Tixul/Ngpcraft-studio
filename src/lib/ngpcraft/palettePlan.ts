/*
 * NgpCraft: ask the V3 for the "NGPC native" palettes of a background
 * (main process). See docs/ETUDE_16_PALETTES.md §7.
 *
 * The mode is offered only when the installed V3 has `palette-plan`
 * (checked once per V3 folder + Python): an older V3 would refuse the
 * ngpc_color key at build.
 */
import Path from "path";
import { spawn } from "child_process";
import { getPython, getV3Root } from "lib/ngpcraft/v3Paths";
import {
  NgpcPalettePlan,
  NgpcSpritePalettePlan,
  parsePalettePlan,
  parseSpritePalettePlan,
} from "shared/lib/ngpcraft/palettePlan";

export type PalettePlanResult = { plan: NgpcPalettePlan } | { error: string };

const run = (
  python: string,
  args: string[],
  cwd: string,
): Promise<{ code: number; stdout: string; stderr: string }> =>
  new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    const child = spawn(python, ["-B", "-m", "ngpcraft", ...args], {
      cwd,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });
    child.stdout.on("data", (d) => {
      stdout += d;
    });
    child.stderr.on("data", (d) => {
      stderr += d;
    });
    child.on("error", (e) => resolve({ code: -1, stdout, stderr: String(e) }));
    child.on("close", (code) => resolve({ code: code ?? -1, stdout, stderr }));
  });

const capabilityCache = new Map<string, Promise<boolean>>();

export const hasNativeColorSupport = async (): Promise<boolean> => {
  const v3Root = await getV3Root();
  const python = getPython();
  const key = `${python}|${v3Root}`;
  if (!capabilityCache.has(key)) {
    capabilityCache.set(
      key,
      run(python, ["palette-plan", "--help"], v3Root).then((r) => r.code === 0),
    );
  }
  return capabilityCache.get(key) as Promise<boolean>;
};

export const readPalettePlan = async (
  projectPath: string,
  backgroundId: string,
): Promise<PalettePlanResult> => {
  if (!(await hasNativeColorSupport())) {
    return { error: "This NgpCraft V3 has no palette-plan command." };
  }
  const result = await run(
    getPython(),
    ["palette-plan", Path.dirname(projectPath), "--background", backgroundId],
    await getV3Root(),
  );
  if (result.code !== 0) {
    return {
      error: result.stderr.trim() || `palette-plan failed (${result.code})`,
    };
  }
  try {
    const plan = parsePalettePlan(JSON.parse(result.stdout));
    return plan
      ? { plan }
      : { error: "palette-plan output does not match ngpcraft-palette-plan/1" };
  } catch (e) {
    return { error: `palette-plan output is not JSON: ${e}` };
  }
};

export const readSpritePalettePlan = async (
  projectPath: string,
  spriteId: string,
): Promise<{ plan: NgpcSpritePalettePlan } | { error: string }> => {
  if (!(await hasNativeColorSupport())) {
    return { error: "This NgpCraft V3 has no palette-plan command." };
  }
  const result = await run(
    getPython(),
    ["palette-plan", Path.dirname(projectPath), "--sprite", spriteId],
    await getV3Root(),
  );
  if (result.code !== 0) {
    return {
      error: result.stderr.trim() || `palette-plan failed (${result.code})`,
    };
  }
  try {
    const plan = parseSpritePalettePlan(JSON.parse(result.stdout));
    return plan
      ? { plan }
      : { error: "palette-plan output does not match ngpcraft-palette-plan/1" };
  } catch (e) {
    return { error: `palette-plan output is not JSON: ${e}` };
  }
};
