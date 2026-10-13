/* eslint-disable camelcase */ // Keys of the V3 conversion policy.
/*
 * NgpCraft: convert a GB Studio .uge song for the NGPC with the V3
 * (`convert-uge`), into <project>/native-audio/<song name>/, and return the
 * held-score link for ngpcraft.json resources.<id>.native_music.
 *
 * Same recipe as the gbs2 demo built by the V3 (2026-10-07): conversion
 * policy and initial APU profile below, and a tonal channel is raised by an
 * octave only when the V3 reports its notes outside the NGPC dividers. The
 * policy, the APU profile and the V3 loss report stay next to the song.
 *
 * A .mod song first goes through GB Studio's own MOD importer (the one of
 * "Convert to UGE", shared/lib/uge/mod2uge, with the song's speed conversion
 * setting), as the V3 gbs2 demo did; the intermediate source.uge is kept in
 * the song's folder. The link stays on the .mod resource the scripts play.
 */
import Path from "path";
import { spawn } from "child_process";
import {
  move,
  outputFile,
  outputJSON,
  pathExists,
  readFile,
  remove,
} from "fs-extra";
import { createHash } from "crypto";
import { convertMODDataToUGESong } from "shared/lib/uge/mod2uge/import";
import { saveUGESong } from "shared/lib/uge/ugeHelper";
import { getPython, getV3Root, v3Env } from "lib/ngpcraft/v3Paths";
import type {
  ConvertUgeResult,
  NgpcNativeMusicHeldScore,
} from "shared/lib/ngpcraft/projectExtension";

export const NATIVE_AUDIO_FOLDER = "native-audio";

// Fingerprint of the song file a conversion was made from, kept in the
// conversion folder: the build converts again when the song has changed.
export const CONVERTED_SOURCE_FILE = "converted-from.json";

export const songFingerprint = async (file: string): Promise<string> =>
  createHash("sha256")
    .update(await readFile(file))
    .digest("hex");

const DEFAULT_POLICY = {
  schema: "ngpcraft-uge-conversion-policy/1",
  tick_mapping: "one-huge-call-per-vblank",
  source_huge_hz: 64,
  wave: "square",
  volume: "relative-amplitude-2db-attenuation",
  hardware_envelope_length_sweep: "discard",
  // NR51 routing kept: each side the song routes a channel to plays it (V3 music_pan.py).
  stereo: "nr51",
  ngps_updates: "retrigger",
  intra_tick: "last-state",
  transpose_semitones: [0, 0, 0],
  noise_setting: 5,
};

const DEFAULT_INITIAL_APU = {
  NR12: 243,
  NR22: 0,
  NR32: 159,
  NR42: 0,
  NR51: 255,
};

// Raise a channel by at most 4 octaves before giving up.
const MAX_TRANSPOSE = 48;
// "channel[2]: quantized key divider outside 1..1023" or
// "channel[2].period[650]: divider 1023.9 outside 1..1023; transpose ...".
const OUT_OF_RANGE = /channel\[(\d)\][^:]*:.*outside 1\.\.1023/;

const runV3 = (
  python: string,
  args: string[],
  cwd: string,
): Promise<{ code: number; stderr: string }> =>
  new Promise((resolve) => {
    let stderr = "";
    const child = spawn(python, ["-B", "-m", "ngpcraft", ...args], {
      cwd,
      env: v3Env(),
    });
    child.stdout.on("data", () => {});
    child.stderr.on("data", (d) => {
      stderr += d;
    });
    child.on("error", (e) => resolve({ code: -1, stderr: String(e) }));
    child.on("close", (code) => resolve({ code: code ?? -1, stderr }));
  });

// Folder name for a song: its file name without extension, kept readable.
export const nativeAudioFolderName = (songFilename: string): string =>
  Path.basename(songFilename)
    .replace(/\.[^.]*$/, "")
    .replace(/[<>:"/\\|?*]/g, "_")
    .trim() || "song";

const toPosix = (path: string) => path.split(Path.sep).join("/");

export const convertUge = async (
  projectRoot: string,
  // Song path relative to the project, as GB Studio stores it.
  songPath: string,
  // .mod only: GB Studio's MOD import speed conversion (song settings).
  modSpeedConversion = true,
): Promise<ConvertUgeResult> => {
  const sourceFile = Path.join(projectRoot, songPath);
  if (!(await pathExists(sourceFile))) {
    return { error: `${songPath}: file not found` };
  }
  const folder = nativeAudioFolderName(songPath);
  const relativeOut = `${NATIVE_AUDIO_FOLDER}/${folder}`;
  const outDir = Path.join(projectRoot, NATIVE_AUDIO_FOLDER, folder);
  const workDir = `${outDir}.converting`;
  const isMod = /\.mod$/i.test(songPath);
  const modUge = `${outDir}.source.uge`;
  if (isMod) {
    try {
      const song = convertMODDataToUGESong(
        await readFile(sourceFile),
        modSpeedConversion,
      );
      await outputFile(modUge, saveUGESong(song));
    } catch (e) {
      return { error: `${songPath}: MOD import failed: ${String(e)}` };
    }
  }
  const songFile = isMod ? modUge : sourceFile;
  const python = getPython();
  const v3Root = await getV3Root();

  const policy = {
    ...DEFAULT_POLICY,
    transpose_semitones: [...DEFAULT_POLICY.transpose_semitones],
  };
  for (;;) {
    await remove(workDir);
    const policyFile = Path.join(workDir, "..", `${folder}.policy.json`);
    const apuFile = Path.join(workDir, "..", `${folder}.initial-apu.json`);
    await outputJSON(policyFile, policy, { spaces: 2 });
    await outputJSON(apuFile, DEFAULT_INITIAL_APU, { spaces: 2 });
    const result = await runV3(
      python,
      [
        "convert-uge",
        "--initial-apu",
        apuFile,
        "--policy",
        policyFile,
        "--out",
        workDir,
        songFile,
      ],
      v3Root,
    );
    await remove(policyFile);
    await remove(apuFile);
    if (result.code === 0) {
      break;
    }
    const outOfRange = OUT_OF_RANGE.exec(result.stderr);
    const channel = outOfRange ? Number(outOfRange[1]) : -1;
    if (
      channel >= 0 &&
      channel < policy.transpose_semitones.length &&
      policy.transpose_semitones[channel] < MAX_TRANSPOSE
    ) {
      policy.transpose_semitones[channel] += 12;
      continue;
    }
    await remove(workDir);
    await remove(modUge);
    return {
      error:
        result.stderr.trim().split("\n").pop() ||
        `convert-uge failed (${result.code})`,
    };
  }

  // Keep the recipe with the song, as the V3 demo does.
  await outputJSON(Path.join(workDir, "policy.json"), policy, { spaces: 2 });
  await outputJSON(
    Path.join(workDir, CONVERTED_SOURCE_FILE),
    { song: songPath, sha256: await songFingerprint(sourceFile) },
    { spaces: 2 },
  );
  await outputJSON(
    Path.join(workDir, "initial-apu.json"),
    DEFAULT_INITIAL_APU,
    {
      spaces: 2,
    },
  );
  if (isMod) {
    await move(modUge, Path.join(workDir, "source.uge"), { overwrite: true });
  }
  await remove(outDir);
  await move(workDir, outDir);

  const binding: NgpcNativeMusicHeldScore = {
    format: "held-score",
    score: toPosix(`${relativeOut}/held-score.json`),
    bank: toPosix(`${relativeOut}/bank.json`),
  };
  return {
    binding,
    folder: relativeOut,
    transposeSemitones: policy.transpose_semitones,
  };
};
