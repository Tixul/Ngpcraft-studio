/*
 * NgpCraft F2 gate: a project built from the editor (loaded into the store
 * reducers, denormalized like the Build button does, then snapshotted by
 * buildWithV3) gives the same ROM as the V3 command line run
 * directly on the project folder.
 *
 * Needs the V3 compiler, Python and the Toshiba toolchain; skipped otherwise.
 * NGPCRAFT_TEST_PROJECT selects another GB Studio project folder.
 */
import Path from "path";
import os from "os";
import { createHash } from "crypto";
import { existsSync, mkdtempSync, readFileSync, readdirSync } from "fs";
import { spawnSync } from "child_process";
import loadProject from "lib/project/loadProjectData";
import { buildWithV3 } from "lib/ngpcraft/v3Build";
import { readNgpcExtension } from "lib/ngpcraft/projectExtension";
import { setNgpcProjectSetting } from "shared/lib/ngpcraft/projectExtension";
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
// A corpus project outside the repository, beside it in
// GitHub/NgpCraft/_Engine/romgb, or NGPCRAFT_TEST_PROJECT.
const projectDir =
  process.env.NGPCRAFT_TEST_PROJECT ||
  Path.join(
    __dirname,
    "../../../NgpCraft/_Engine/romgb/corpus-20261008/projects/menu",
  );

const available =
  existsSync(Path.join(v3Root, "ngpcraft")) &&
  existsSync(toshibaHome) &&
  existsSync(projectDir);

const sha256 = (path: string) =>
  createHash("sha256").update(readFileSync(path)).digest("hex");

const runCli = (args: string[]) => {
  const result = spawnSync(
    process.env.NGPCRAFT_PYTHON || "python",
    ["-B", "-m", "ngpcraft", ...args],
    {
      cwd: v3Root,
      env: { ...process.env, NGPC_TOSHIBA_HOME: toshibaHome },
      encoding: "utf8",
    },
  );
  if (result.status !== 0) {
    throw new Error(result.stderr);
  }
};

(available ? describe : describe.skip)("NgpCraft V3 build", () => {
  test("editor build matches the V3 command line ROM", async () => {
    const tmp = mkdtempSync(Path.join(os.tmpdir(), "ngpcraft-f2-"));

    const cliOut = Path.join(tmp, "cli");
    runCli(["export", projectDir, "--out", cliOut]);
    runCli(["build-rom", cliOut]);
    const cliRom = Path.join(cliOut, "rom", "game.ngc");

    const projectFile = readdirSync(projectDir).find((name) =>
      name.endsWith(".gbsproj"),
    );
    expect(projectFile).toBeDefined();
    const loaded = await loadProject(
      Path.join(projectDir, projectFile as string),
    );
    const loadedAction = projectActions.loadProject.fulfilled(
      { ...loaded, path: projectFile as string },
      "ngpcraft-f2",
      projectFile as string,
    );
    const project = denormalizeProject({
      entities: entitiesReducer(entitiesInitialState, loadedAction),
      settings: settingsReducer(settingsInitialState, loadedAction),
      metadata: metadataReducer(metadataInitialState, loadedAction),
    });
    const logs: string[] = [];
    const result = await buildWithV3(project, {
      projectRoot: projectDir,
      outputRoot: Path.join(tmp, "editor"),
      progress: (msg) => logs.push(msg),
      warnings: (msg) => logs.push(msg),
    });

    expect(sha256(result.romPath)).toBe(sha256(cliRom));
    // Budget read from the V3 report, one entry per built scene.
    expect(result.scenes.length).toBeGreaterThan(0);
    for (const scene of result.scenes) {
      expect(scene.usedTiles).toBeGreaterThan(0);
      expect(scene.usedTiles).toBeLessThanOrEqual(512);
    }

    // F7: an NGPC setting edited in the editor (not saved) reaches the V3.
    const diskExtension = Path.join(projectDir, "ngpcraft.json");
    const diskBefore = existsSync(diskExtension)
      ? readFileSync(diskExtension, "utf8")
      : null;
    const at30 = await buildWithV3(project, {
      projectRoot: projectDir,
      outputRoot: Path.join(tmp, "editor30"),
      progress: (msg) => logs.push(msg),
      warnings: (msg) => logs.push(msg),
      ngpcraftExtension: setNgpcProjectSetting(
        await readNgpcExtension(projectDir),
        "frame_rate",
        30,
      ),
    });
    expect(sha256(at30.romPath)).not.toBe(sha256(cliRom));
    const diskAfter = existsSync(diskExtension)
      ? readFileSync(diskExtension, "utf8")
      : null;
    expect(diskAfter).toBe(diskBefore);
  }, 300000);
});
