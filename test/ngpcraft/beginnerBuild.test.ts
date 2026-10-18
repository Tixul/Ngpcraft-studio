/*
 * NgpCraft gate: a beginner following a GB Studio tutorial starts from a
 * template and presses Play. Nothing NGPC-specific is done: the build
 * converts the songs and sound effect files itself (autoConvertMusic,
 * autoConvertSounds, as main.ts does before buildWithV3).
 *
 * Needs the V3 compiler, Python and the Toshiba toolchain; skipped otherwise.
 */
import Path from "path";
import os from "os";
import { existsSync, mkdtempSync, statSync } from "fs";
import createProject from "lib/project/createProject";
import loadProject from "lib/project/loadProjectData";
import { buildWithV3 } from "lib/ngpcraft/v3Build";
import {
  autoConvertMusic,
  autoConvertSounds,
} from "lib/ngpcraft/autoConvertMusic";
import { readNgpcExtension } from "lib/ngpcraft/projectExtension";
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
    "ngpcraft-beginner",
    projectFile,
  );
  return denormalizeProject({
    entities: entitiesReducer(entitiesInitialState, action),
    settings: settingsReducer(settingsInitialState, action),
    metadata: metadataReducer(metadataInitialState, action),
  });
};

// "blank" has no scene: GB Studio refuses it too ("No scenes are included
// in your project"), and so does the V3.
describe.each(["gbhtml", "gbs2", "ngpc", "ngpc_ninja_run"])("template %s", (template) => {
  (available ? test : test.skip)(
    "builds with no NGPC step",
    async () => {
      const tmp = mkdtempSync(Path.join(os.tmpdir(), `ngpcraft-${template}-`));
      const projectFile = await createProject({
        name: "Demo",
        template,
        path: tmp,
      });
      const projectRoot = Path.dirname(projectFile);
      const project = await load(projectFile);
      const logs: string[] = [];
      const log = (m: string) => logs.push(m);
      const music = await autoConvertMusic(
        projectRoot,
        project.music,
        await readNgpcExtension(projectRoot),
        log,
      );
      const sounds = await autoConvertSounds(
        projectRoot,
        project.sounds,
        music.extension,
        log,
      );
      const result = await buildWithV3(project, {
        projectRoot,
        outputRoot: Path.join(tmp, "build"),
        ngpcraftExtension: sounds.extension,
        progress: () => {},
        warnings: log,
      }).catch((e) => {
        throw new Error([e.message, ...logs].join("\n"));
      });
      expect(statSync(result.romPath).size).toBeGreaterThan(0);
      expect(logs.filter((m) => m.includes("could not be converted"))).toEqual(
        [],
      );
    },
    900000,
  );
});
