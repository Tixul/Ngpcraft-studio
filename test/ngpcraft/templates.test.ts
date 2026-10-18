/*
 * NgpCraft gate: a new project made from each project template (as the
 * New Project screen does) builds a ROM with the V3, through the editor
 * path (store reducers, denormalize, snapshot).
 *
 * Needs the V3 compiler, Python and the Toshiba toolchain; skipped otherwise.
 * The blank template has no scene, so it is not built (GB Studio refuses it
 * too: "No scenes are included in your project").
 * NGPCRAFT_TEST_TEMPLATES limits the run, e.g. "gbhtml".
 * NGPCRAFT_TEST_ROM_OUT=<folder> keeps each ROM (<template>.ngc) to run it.
 */
import Path from "path";
import os from "os";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, statSync } from "fs";
import createProject from "lib/project/createProject";
import loadProject from "lib/project/loadProjectData";
import { buildWithV3 } from "lib/ngpcraft/v3Build";
import { readNgpcExtension } from "lib/ngpcraft/projectExtension";
import { convertUge } from "lib/ngpcraft/convertUge";
import { setNgpcResourceSetting } from "shared/lib/ngpcraft/projectExtension";
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

const templates = (
  process.env.NGPCRAFT_TEST_TEMPLATES || "gbhtml,gbs2,ngpc,ngpc_ninja_run"
).split(",");

(available ? describe : describe.skip)("NgpCraft project templates", () => {
  test.each(templates)(
    "a new project from the %s template builds",
    async (template) => {
      const tmp = mkdtempSync(Path.join(os.tmpdir(), "ngpcraft-tpl-"));
      const projectFile = await createProject({
        name: "Test",
        template,
        path: tmp,
      });
      const loaded = await loadProject(projectFile);
      const loadedAction = projectActions.loadProject.fulfilled(
        { ...loaded, path: projectFile },
        "ngpcraft-templates",
        projectFile,
      );
      const project = denormalizeProject({
        entities: entitiesReducer(entitiesInitialState, loadedAction),
        settings: settingsReducer(settingsInitialState, loadedAction),
        metadata: metadataReducer(metadataInitialState, loadedAction),
      });
      const projectRoot = Path.dirname(projectFile);
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
      const keep = process.env.NGPCRAFT_TEST_ROM_OUT;
      if (keep) {
        mkdirSync(keep, { recursive: true });
        copyFileSync(result.romPath, Path.join(keep, `${template}.ngc`));
      }
      // Scene budgets reach the scene sidebar: one per built scene, with the
      // view height. Every template scene fills the NGPC screen (gbs2 /
      // gbhtml backgrounds were extended to 152 px on 2026-10-10): no scene
      // is drawn between the 4 px bands of a 144 px GB screen.
      expect(result.scenes.length).toBeGreaterThan(0);
      expect(
        result.scenes.filter((scene) => scene.viewHeight !== 152),
      ).toEqual([]);
    },
    600000,
  );

  // Convert for the NGPC, then Listen: the editor's path for a song (the
  // conversion is redone here, over the one shipped in the template).
  test("Listen: a converted gbs2 song plays alone in a ROM", async () => {
    const tmp = mkdtempSync(Path.join(os.tmpdir(), "ngpcraft-listen-"));
    const projectFile = await createProject({
      name: "Test",
      template: "gbs2",
      path: tmp,
    });
    const loaded = await loadProject(projectFile);
    const loadedAction = projectActions.loadProject.fulfilled(
      { ...loaded, path: projectFile },
      "ngpcraft-listen",
      projectFile,
    );
    const project = denormalizeProject({
      entities: entitiesReducer(entitiesInitialState, loadedAction),
      settings: settingsReducer(settingsInitialState, loadedAction),
      metadata: metadataReducer(metadataInitialState, loadedAction),
    });
    const projectRoot = Path.dirname(projectFile);
    const song = project.music.find(
      (music) => music.filename === "Rulz_GonaSpace.uge",
    );
    expect(song).toBeDefined();
    const songId = song?.id ?? "";
    const converted = await convertUge(
      projectRoot,
      "assets/music/Rulz_GonaSpace.uge",
    );
    if ("error" in converted) {
      throw new Error(converted.error);
    }
    const extension = setNgpcResourceSetting(
      await readNgpcExtension(projectRoot),
      songId,
      "native_music",
      converted.binding,
    );
    const logs: string[] = [];
    const result = await buildWithV3(project, {
      projectRoot,
      outputRoot: Path.join(tmp, "build"),
      ngpcraftExtension: extension,
      previewMusicId: songId,
      progress: () => {},
      warnings: (msg) => logs.push(msg),
    }).catch((e) => {
      throw new Error([e.message, ...logs].join("\n"));
    });
    expect(statSync(result.romPath).size).toBeGreaterThan(0);
    expect(result.scenes).toHaveLength(1);
  }, 600000);
});
