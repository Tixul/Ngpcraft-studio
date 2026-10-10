/*
 * NgpCraft gate: GB Studio's sample project (gbs2 template; its scenes and
 * scripts are GB Studio's) becomes an NGPC ROM the way a user does it in the
 * editor, without relying on the audio shipped in the template: a new project
 * from the template, "Convert for the NGPC" on every song (.uge, .mod) and every
 * sound effect bank, then the build. Same calls as the editor buttons
 * (lib/ngpcraft/convertUge, convertSfx), same ngpcraft.json writer.
 *
 * Needs the V3 compiler, Python and the Toshiba toolchain; skipped otherwise.
 * NGPCRAFT_TEST_ROM_OUT=<folder> keeps the ROM (gbs2.ngc) to run it.
 */
import Path from "path";
import os from "os";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, statSync } from "fs";
import createProject from "lib/project/createProject";
import loadProject from "lib/project/loadProjectData";
import { buildWithV3 } from "lib/ngpcraft/v3Build";
import { convertUge } from "lib/ngpcraft/convertUge";
import { convertSfx } from "lib/ngpcraft/convertSfx";
import {
  readNgpcExtension,
  writeNgpcExtension,
} from "lib/ngpcraft/projectExtension";
import {
  setNgpcResourceSetting,
  setNgpcSoundEffects,
} from "shared/lib/ngpcraft/projectExtension";
import projectActions, {
  denormalizeProject,
} from "store/features/project/projectActions";
import entitiesReducer, {
  initialState as entitiesInitialState,
} from "store/features/entities/entitiesState";
import settingsReducer, {
  initialState as settingsInitialState,
} from "store/features/settings/settingsState";
import metadataReducer, {
  initialState as metadataInitialState,
} from "store/features/metadata/metadataState";
import { ngpcraftDefaultV3Root } from "consts";

const v3Root = process.env.NGPCRAFT_V3_HOME || ngpcraftDefaultV3Root;
const toshibaHome = process.env.NGPC_TOSHIBA_HOME || "C:\\t900";
const available =
  existsSync(Path.join(v3Root, "ngpcraft")) && existsSync(toshibaHome);

const load = async (projectFile: string) => {
  const loaded = await loadProject(projectFile);
  const action = projectActions.loadProject.fulfilled(
    { ...loaded, path: projectFile },
    "ngpcraft-gbs2-demo",
    projectFile,
  );
  return denormalizeProject({
    entities: entitiesReducer(entitiesInitialState, action),
    settings: settingsReducer(settingsInitialState, action),
    metadata: metadataReducer(metadataInitialState, action),
  });
};

(available ? test : test.skip)(
  "the GB Studio sample project, converted for the NGPC, builds",
  async () => {
    const tmp = mkdtempSync(Path.join(os.tmpdir(), "ngpcraft-gbs2-"));
    const projectFile = await createProject({
      name: "Demo",
      template: "gbs2",
      path: tmp,
    });
    const projectRoot = Path.dirname(projectFile);
    const before = await load(projectFile);
    let extension = await readNgpcExtension(projectRoot);

    // Every song, .uge and .mod (the radio of Top House plays template.mod).
    const songs = before.music;
    expect(songs.some((m) => m.type === "mod")).toBe(true);
    for (const song of songs) {
      const result = await convertUge(
        projectRoot,
        `assets/music/${song.filename}`,
        song.type === "mod" ? !song.settings?.disableSpeedConversion : undefined,
      );
      if ("error" in result) {
        throw new Error(`${song.filename}: ${result.error}`);
      }
      extension = setNgpcResourceSetting(
        extension,
        song.id,
        "native_music",
        result.binding,
      );
    }
    for (const sound of before.sounds) {
      // As NgpcSfxBinding: every effect of an FX Hammer bank, else effect 0.
      const total =
        sound.type === "fxhammer" ? Math.max(1, sound.numEffects ?? 1) : 1;
      const result = await convertSfx(
        Path.join(projectRoot, "assets", "sounds", sound.filename),
        total,
      );
      if ("error" in result) {
        throw new Error(`${sound.filename}: ${result.error}`);
      }
      extension = setNgpcSoundEffects(extension, sound.id, result.timelines);
    }
    await writeNgpcExtension(projectRoot, extension);

    const project = await load(projectFile);
    const logs: string[] = [];
    const result = await buildWithV3(project, {
      projectRoot,
      outputRoot: Path.join(tmp, "build"),
      ngpcraftExtension: await readNgpcExtension(projectRoot),
      progress: () => {},
      warnings: (msg) => logs.push(msg),
    }).catch((e) => {
      throw new Error([e.message, ...logs].join("\n"));
    });
    expect(statSync(result.romPath).size).toBeGreaterThan(0);
    expect(result.scenes.length).toBe(project.scenes.length);
    const keep = process.env.NGPCRAFT_TEST_ROM_OUT;
    if (keep) {
      mkdirSync(keep, { recursive: true });
      copyFileSync(result.romPath, Path.join(keep, "gbs2.ngc"));
    }
  },
  900000,
);
