/*
 * NgpCraft: convert a GB Studio sound effect file for the NGPC with the V3
 * (`convert-sfx`, ngpcraft-v3/docs/M7_SFX_CONVERT.md): one native timeline
 * per effect (an FX Hammer bank holds several, a VGM one), bound in
 * ngpcraft.json ngpc.native_sfx under "<sound id>:<effect>". WAV effects
 * (sample playback) are refused by the V3 with its reason.
 */
import { spawn } from "child_process";
import { getPython, getV3Root } from "lib/ngpcraft/v3Paths";
import type {
  ConvertSfxResult,
  NgpcNativeSfxStep,
} from "shared/lib/ngpcraft/projectExtension";

const runV3 = (
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

export const convertSfx = async (
  soundPath: string,
  effects: number,
): Promise<ConvertSfxResult> => {
  const python = getPython();
  const v3Root = await getV3Root();
  const timelines: NgpcNativeSfxStep[][] = [];
  const losses = new Set<string>();
  for (let effect = 0; effect < Math.max(1, effects); effect++) {
    const result = await runV3(
      python,
      ["convert-sfx", soundPath, "--effect", String(effect)],
      v3Root,
    );
    if (result.code !== 0) {
      return {
        error:
          result.stderr.trim().replace(/^error: /, "") ||
          `convert-sfx failed (${result.code})`,
      };
    }
    try {
      const output = JSON.parse(result.stdout);
      if (output.format !== "ngpcraft-native-sfx/1") {
        return { error: "convert-sfx output is not ngpcraft-native-sfx/1" };
      }
      timelines.push(output.rows);
      for (const loss of output.losses ?? []) {
        losses.add(String(loss));
      }
    } catch (e) {
      return { error: `convert-sfx output is not JSON: ${e}` };
    }
  }
  return { timelines, losses: Array.from(losses) };
};
