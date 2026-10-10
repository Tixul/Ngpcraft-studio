/* eslint-disable camelcase */ // Keys of V3 / GB Studio file formats.
/*
 * NgpCraft F8: the native music link edited in the editor reaches the V3.
 * Project: SLATES battle (two songs, each with song.ngps and bank.json).
 * Needs the V3 compiler and the Toshiba toolchain; skipped otherwise.
 */
import Path from "path";
import os from "os";
import { existsSync, mkdtempSync, readdirSync } from "fs";
import loadProject from "lib/project/loadProjectData";
import { buildWithV3 } from "lib/ngpcraft/v3Build";
import { readNgpcExtension } from "lib/ngpcraft/projectExtension";
import {
  getNgpcResourceSetting,
  NgpcProjectExtension,
  setNgpcResourceSetting,
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
// A game project (SLATES battle), outside the repository: beside it in
// GitHub/NgpCraft/_Engine/romgb, or NGPCRAFT_TEST_SLATES.
const projectDir =
  process.env.NGPCRAFT_TEST_SLATES ||
  Path.join(
    __dirname,
    "../../../NgpCraft/_Engine/romgb/expressions-20261008/projects/slates-battle",
  );
// Battle music, played by the start scene (an unreached song is not required).
const SONG_ID = "99255a3e-1fcc-4da4-bc04-b9da6e98b376";
// A Sound Creator song the V3 compiles as .ngps. ("Free OST 1 - Fighting"
// has a held note across its loop, which the driver cannot play: the V3
// refuses it, which is why the project uses its converted held score.)
const SONG_DIR = "native-audio/Great Melody 01";

const available =
  existsSync(Path.join(v3Root, "ngpcraft")) &&
  existsSync(toshibaHome) &&
  existsSync(Path.join(projectDir, SONG_DIR, "song.ngps"));

const loadEditorProject = async () => {
  const projectFile = readdirSync(projectDir).find((name) =>
    name.endsWith(".gbsproj"),
  ) as string;
  const loaded = await loadProject(Path.join(projectDir, projectFile));
  const action = projectActions.loadProject.fulfilled(
    { ...loaded, path: projectFile },
    "ngpcraft-f8",
    projectFile,
  );
  return denormalizeProject({
    entities: entitiesReducer(entitiesInitialState, action),
    settings: settingsReducer(settingsInitialState, action),
    metadata: metadataReducer(metadataInitialState, action),
  });
};

(available ? describe : describe.skip)("NgpCraft native music link", () => {
  test("unlinked song is refused, a .ngps link builds", async () => {
    const project = await loadEditorProject();
    const extension = (await readNgpcExtension(
      projectDir,
    )) as NgpcProjectExtension;
    expect(
      getNgpcResourceSetting(extension, SONG_ID, "native_music"),
    ).toBeDefined();
    const tmp = mkdtempSync(Path.join(os.tmpdir(), "ngpcraft-f8-"));
    const build = (ngpcraftExtension: NgpcProjectExtension, name: string) =>
      buildWithV3(project, {
        projectRoot: projectDir,
        outputRoot: Path.join(tmp, name),
        progress: () => {},
        warnings: () => {},
        ngpcraftExtension,
      });

    // Unlinked (Remove link in the bar): the V3 refuses the played song.
    await expect(
      build(
        setNgpcResourceSetting(extension, SONG_ID, "native_music", undefined),
        "unlinked",
      ),
    ).rejects.toThrow();

    // Linked to the Sound Creator song, as the bar writes it.
    const linked = await build(
      setNgpcResourceSetting(extension, SONG_ID, "native_music", {
        format: "ngps",
        song: `${SONG_DIR}/song.ngps`,
        bank: `${SONG_DIR}/bank.json`,
        // eslint-disable-next-line camelcase
        note_encoding: "sound-creator",
        // eslint-disable-next-line camelcase
        tick_clock: "vblank",
      }),
      "ngps",
    );
    expect(existsSync(linked.romPath)).toBe(true);
  }, 300000);
});

test("resource settings are set and removed without touching others", () => {
  const extension = {
    schema: "ngpcraft-project/1",
    ngpc: { frame_rate: 30 },
    resources: { other: { second_background: { background_id: "b" } } },
  };
  const linked = setNgpcResourceSetting(extension, "song", "native_music", {
    format: "ngps",
  });
  expect(getNgpcResourceSetting(linked, "song", "native_music")).toEqual({
    format: "ngps",
  });
  expect(linked.ngpc).toEqual(extension.ngpc);
  const unlinked = setNgpcResourceSetting(
    linked,
    "song",
    "native_music",
    undefined,
  );
  // The emptied resource entry disappears, the others stay.
  expect(unlinked.resources).toEqual(extension.resources);
});
