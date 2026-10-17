/*
 * NgpCraft: build a Neo Geo Pocket Color ROM with the V3 compiler.
 *
 * The V3 reads a GB Studio project from disk. To build what the user sees
 * (unsaved edits, "play from this scene"), the in-memory project is written
 * to a snapshot folder with GB Studio's own save encoder, next to a copy of
 * every other project file (assets, plugins, ngpcraft.json and what it
 * references, such as native audio). The V3 then runs on that
 * snapshot: `export` then `build-rom`. The user's project is never written.
 */
import type { NgpcDebugInfo } from "shared/lib/debugger/types";
import Path from "path";
import {
  copy,
  ensureDir,
  outputFile,
  pathExists,
  readJSON,
  readdir,
  remove,
} from "fs-extra";
import { FontResource, ProjectResources } from "shared/lib/resources/types";
import { compressProjectResources } from "shared/lib/resources/compression";
import {
  buildResourceExportBuffer,
  encodeResource,
} from "shared/lib/resources/save";
import spawn, { ChildProcess } from "lib/helpers/cli/spawn";
import {
  encodeNgpcExtension,
  NGPC_EXTENSION_FILENAME,
} from "lib/ngpcraft/projectExtension";
import {
  NgpcProjectExtension,
  projectResourceIds,
  withoutOrphanResources,
} from "shared/lib/ngpcraft/projectExtension";
import type { NgpcSceneBudget } from "shared/lib/ngpcraft/sceneBudget";
import {
  getPython,
  getToolchainPath,
  getV3Root,
  v3Env,
} from "lib/ngpcraft/v3Paths";

interface V3Tools {
  v3Root: string;
  python: string;
  toolchainHome: string;
}

// Top-level project entries never copied: build outputs and version control.
const SKIPPED_ENTRIES = new Set(["build", ".git"]);

// Project files written from memory instead of copied, as a save would.
const isProjectResourceFile = (path: string) =>
  path.endsWith(".gbsres") || path.endsWith(".gbsproj");

// Fonts in memory also carry `mapping` and `table`, read from the .json next
// to the image. GB Studio writes them only into a font file it rewrites, so
// its own templates and some projects have `mapping` in the .gbsres; the V3
// accepts only the published schema and reads the .json itself.
const fontResourceKeys = Object.keys(FontResource.properties);
const withoutFontMetadata = (font: FontResource): FontResource =>
  Object.fromEntries(
    Object.entries(font).filter(([key]) => fontResourceKeys.includes(key)),
  ) as FontResource;

interface V3BuildOptions {
  projectRoot: string;
  outputRoot: string;
  // In-memory ngpcraft.json from the editor; replaces the file on disk.
  ngpcraftExtension?: NgpcProjectExtension | null;
  // Debug build (export --debug): event symbols and public VM contexts for
  // the debugger panel (ngpcraft-v3/docs/M7_DEBUG.md).
  debug?: boolean;
  // A ROM that only plays this song (V3 preview-music, music_preview.py).
  previewMusicId?: string;
  progress: (msg: string) => void;
  warnings: (msg: string) => void;
}

// Per-scene budget read from the V3 export report (scene_engine.scenes),
// in V3 scene index order (ngc_scene). Character RAM order in the V3:
// reserved (font/UI), background, sprites.
export type V3SceneBudget = NgpcSceneBudget;

export interface V3BuildResult {
  romPath: string;
  romSize: number;
  title: string;
  warningCount: number;
  scenes: V3SceneBudget[];
  // Debugger: GBVM global variable indices (symbol -> index) and the console
  // address of _script_memory (16-bit values), when the runtime links it.
  variables: Record<string, number>;
  scriptMemoryAddress?: number;
  // Console address of ngc_scene, the index of the current scene.
  sceneIndexAddress?: number;
  // Debug builds only.
  debug?: NgpcDebugInfo;
  // Script sources by "<symbol>.s" (the debugger's GBVM view).
  gbvmScripts: Record<string, string>;
}

interface V3ExportScene {
  id?: string;
  scene?: string;
  background?: {
    tile_base?: number;
    tile_count?: number;
    palette_counts?: number[];
  };
  sprite_tiles?: number;
  scr2_palettes?: number;
  actors?: unknown[];
  projectiles?: { capacity?: number; pool_ram_bytes?: number };
  view?: { height?: number };
  warnings?: unknown[];
}

const readSceneBudgets = async (
  exportRoot: string,
): Promise<V3SceneBudget[]> => {
  const manifest = await readJSON(Path.join(exportRoot, "export.json"));
  const engine = manifest?.scene_engine;
  // Several scenes: scene_engine.scenes; one scene: scene_engine is its
  // report, the start scene's (no id in it).
  const scenes: V3ExportScene[] =
    engine?.scenes ??
    (engine?.scene
      ? [{ ...engine, id: engine.id ?? manifest?.project?.start_scene }]
      : []);
  return scenes.map((scene) => {
    const tileBase = scene.background?.tile_base ?? 0;
    const backgroundTiles = scene.background?.tile_count ?? 0;
    const spriteTiles = scene.sprite_tiles ?? 0;
    return {
      id: scene.id ?? "",
      name: scene.scene ?? "?",
      usedTiles: tileBase + backgroundTiles + spriteTiles,
      backgroundTiles,
      spriteTiles,
      // scr2_palettes is reported with text only; else the decor's planes.
      backgroundPalettes:
        scene.scr2_palettes ??
        Math.max(0, ...(scene.background?.palette_counts ?? [])),
      actors: scene.actors?.length ?? 0,
      projectiles:
        scene.projectiles?.capacity !== undefined
          ? {
              capacity: scene.projectiles.capacity,
              ramBytes: scene.projectiles.pool_ram_bytes ?? 0,
            }
          : undefined,
      viewHeight: scene.view?.height ?? 152,
      warnings: (scene.warnings ?? []).filter(
        (w): w is string => typeof w === "string",
      ),
    };
  });
};

let childInProgress: ChildProcess | undefined;
let cancelled = false;

export const cancelV3Build = () => {
  cancelled = true;
  childInProgress?.kill();
};

const writeSnapshot = async (
  project: ProjectResources,
  projectRoot: string,
  snapshotRoot: string,
  ngpcraftExtension: NgpcProjectExtension | null | undefined,
  warn: (message: string) => void,
) => {
  await ensureDir(snapshotRoot);
  for (const entry of await readdir(projectRoot)) {
    if (SKIPPED_ENTRIES.has(entry) || isProjectResourceFile(entry)) {
      continue;
    }
    await copy(Path.join(projectRoot, entry), Path.join(snapshotRoot, entry), {
      filter: (src) => !isProjectResourceFile(src),
    });
  }
  const writeBuffer = buildResourceExportBuffer(
    compressProjectResources({
      ...project,
      fonts: project.fonts.map(withoutFontMetadata),
    }),
  );
  for (const { path, data } of writeBuffer) {
    await outputFile(Path.join(snapshotRoot, path), data);
  }
  await outputFile(
    Path.join(snapshotRoot, "project.gbsproj"),
    encodeResource("project", project.metadata),
  );
  if (ngpcraftExtension) {
    // Settings left by a deleted scene or asset: not sent (the V3 refuses
    // unknown ids), and reported.
    const { extension, removed } = withoutOrphanResources(
      ngpcraftExtension,
      projectResourceIds(project),
    );
    for (const id of removed) {
      warn(
        `NgpCraft: ngpcraft.json has settings for ${id}, which is no longer in the project (deleted or recreated); they are ignored.`,
      );
    }
    await outputFile(
      Path.join(snapshotRoot, NGPC_EXTENSION_FILENAME),
      encodeNgpcExtension(extension),
    );
  }
};

const runV3 = async (
  args: string[],
  { progress, warnings }: V3BuildOptions,
  tools: V3Tools,
): Promise<string> => {
  if (cancelled) {
    throw new Error("BUILD_CANCELLED");
  }
  let stdout = "";
  const errors: string[] = [];
  const { child, completed } = spawn(
    tools.python,
    ["-B", "-m", "ngpcraft", ...args],
    {
      cwd: tools.v3Root,
      env: v3Env({ NGPC_TOSHIBA_HOME: tools.toolchainHome }),
    },
    {
      onLog: (line) => {
        stdout += line + "\n";
      },
      onError: (line) => {
        if (!line) {
          return;
        }
        if (line.startsWith("warning:")) {
          warnings(line);
        } else {
          errors.push(line);
        }
      },
    },
  );
  childInProgress = child;
  try {
    await completed;
  } catch (e) {
    if (cancelled) {
      throw new Error("BUILD_CANCELLED");
    }
    for (const line of errors) {
      warnings(line);
    }
    if (errors.some((line) => line.includes("ENOENT"))) {
      throw new Error(
        `Python not found: "${tools.python}". Install Python 3.10+ or set its path in Preferences > NgpCraft.`,
      );
    }
    throw new Error(`NgpCraft V3 ${args[0]} failed (code ${e})`);
  } finally {
    childInProgress = undefined;
  }
  for (const line of errors) {
    progress(line);
  }
  return stdout;
};

export const buildWithV3 = async (
  project: ProjectResources,
  options: V3BuildOptions,
): Promise<V3BuildResult> => {
  cancelled = false;
  const { projectRoot, outputRoot, progress } = options;
  const v3Root = await getV3Root();
  if (!(await pathExists(Path.join(v3Root, "ngpcraft")))) {
    throw new Error(
      `NgpCraft V3 not found in ${v3Root}. Set its folder in Preferences > NgpCraft.`,
    );
  }
  if (!getToolchainPath()) {
    throw new Error(
      "Toshiba toolchain not set. Set its folder in Preferences > NgpCraft.",
    );
  }
  const tools: V3Tools = {
    v3Root,
    python: getPython(),
    toolchainHome: getToolchainPath(),
  };
  const snapshotRoot = Path.join(outputRoot, "project");
  let exportRoot = Path.join(outputRoot, "v3");
  // The output folder is reused for every build of the session, and the V3
  // export refuses a non-empty destination.
  await remove(snapshotRoot);
  await remove(exportRoot);
  await remove(Path.join(outputRoot, "preview"));

  progress(
    `NgpCraft V3: ${tools.v3Root} | toolchain: ${getToolchainPath()} | python: ${tools.python}`,
  );
  progress("NgpCraft: writing project snapshot...");
  await writeSnapshot(
    project,
    projectRoot,
    snapshotRoot,
    options.ngpcraftExtension,
    options.warnings,
  );

  let warningCount = 0;
  const countedOptions = {
    ...options,
    warnings: (msg: string) => {
      if (msg.startsWith("warning:")) {
        warningCount++;
      }
      options.warnings(msg);
    },
  };

  if (options.previewMusicId) {
    progress("NgpCraft V3: music preview...");
    await runV3(
      [
        "preview-music",
        snapshotRoot,
        "--music",
        options.previewMusicId,
        "--out",
        Path.join(outputRoot, "preview"),
      ],
      countedOptions,
      tools,
    );
    exportRoot = Path.join(outputRoot, "preview", "export");
  } else {
    progress("NgpCraft V3: export...");
    await runV3(
      [
        "export",
        snapshotRoot,
        "--out",
        exportRoot,
        ...(options.debug ? ["--debug"] : []),
      ],
      countedOptions,
      tools,
    );
  }

  progress("NgpCraft V3: build-rom...");
  await runV3(["build-rom", exportRoot], countedOptions, tools);

  const report = await readJSON(
    Path.join(exportRoot, "rom", "build-report.json"),
  );
  const manifest = await readJSON(Path.join(exportRoot, "export.json"));
  const scripts: Record<string, { source: string }> = manifest.scripts ?? {};
  const gbvmScripts = Object.fromEntries(
    Object.entries(scripts).map(([symbol, s]) => [`${symbol}.s`, s.source]),
  );
  const debug: NgpcDebugInfo | undefined =
    manifest.debug && report.debug
      ? {
          labels: manifest.debug.labels.map(
            (l: { symbol: string; address: number }) => ({
              symbol: l.symbol,
              address: l.address,
            }),
          ),
          stepContext: manifest.debug.step_context,
          layout: report.debug,
        }
      : undefined;
  return {
    romPath: Path.join(exportRoot, report.rom),
    romSize: report.header.size_bytes,
    title: String(report.header.title).trim(),
    warningCount,
    scenes: await readSceneBudgets(exportRoot),
    variables: manifest.variables ?? {},
    scriptMemoryAddress:
      report.link_audit?.public_symbols?._script_memory?.address,
    sceneIndexAddress: report.link_audit?.public_symbols?._ngc_scene?.address,
    debug,
    gbvmScripts,
  };
};
