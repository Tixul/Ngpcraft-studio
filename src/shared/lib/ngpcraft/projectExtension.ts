/*
 * NgpCraft: ngpcraft.json, the NGPC extensions of a GB Studio project
 * (V3 docs/FORMAT_PROJET.md, schema ngpcraft-project/1). GB Studio files are
 * never touched: NGPC data lives only in this file. The editor edits the
 * project-level settings below and keeps every other key as it was read
 * (native audio, second background, resources...).
 */

export const NGPC_PROJECT_SCHEMA = "ngpcraft-project/1";

export type NgpcProjectExtension = Record<string, unknown>;

export interface NgpcProjectSettings {
  // Role of a long press on Option; the short press takes the other.
  option_long_press?: "select" | "start";
  // Maximum NPCs per scene, 20 (GB Studio) to 30.
  actor_capacity?: number;
  // Live projectiles at once, 5 (GB Studio) to 64.
  projectile_capacity?: number;
  // Fixed frame rate, 60 or 30.
  frame_rate?: 60 | 30;
  // Script context pool: absent = computed by the V3 (never under GB
  // Studio's 16 when a game needs more, M4_CONTEXT_BUDGET.md); "gbvm-16" =
  // exactly GBVM's 16.
  vm_context_policy?: "gbvm-16";
  // Colours of backgrounds and sprite sheets by default: "native" = their own
  // PNG colours (V3 docs/M7_NATIVE_COLOR.md); absent = GB Studio palettes.
  // A resource's ngpc_color ("native" / "gbs") overrides it.
  default_color?: "native";
}

export type NgpcProjectSettingKey = keyof NgpcProjectSettings;

export const NGPC_ACTOR_CAPACITY_MIN = 20;
export const NGPC_ACTOR_CAPACITY_MAX = 30;
// V3 projectile_runtime.py GBS_PROJECTILE_CAPACITY / NGPC_PROJECTILE_CAPACITY.
export const NGPC_PROJECTILE_CAPACITY_MIN = 5;
export const NGPC_PROJECTILE_CAPACITY_MAX = 64;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export const getNgpcProjectSettings = (
  extension: NgpcProjectExtension | null,
): NgpcProjectSettings => {
  const ngpc = extension?.ngpc;
  return isRecord(ngpc) ? (ngpc as NgpcProjectSettings) : {};
};

// Every resource id of a project (ProjectResources), the set the V3 checks
// ngpcraft.json resources.<id> against: top-level resources, scene
// actors/triggers.
export const projectResourceIds = (project: {
  scenes: readonly { actors: readonly unknown[]; triggers: readonly unknown[] }[];
}): Set<string> => {
  const ids = new Set<string>();
  const add = (item: unknown) => {
    if (item && typeof item === "object" && "id" in item) {
      const id = (item as { id: unknown }).id;
      if (typeof id === "string") {
        ids.add(id);
      }
    }
  };
  for (const value of Object.values(project)) {
    if (Array.isArray(value)) {
      value.forEach(add);
    }
  }
  for (const scene of project.scenes) {
    scene.actors.forEach(add);
    scene.triggers.forEach(add);
  }
  return ids;
};

// The extension without the resources.<id> entries of resources the project
// no longer has (a scene or asset deleted after an NGPC setting was given to
// it): the V3 refuses unknown ids. `removed` lists them, to be reported.
export const withoutOrphanResources = (
  extension: NgpcProjectExtension,
  ids: ReadonlySet<string>,
): { extension: NgpcProjectExtension; removed: string[] } => {
  const resources = extension.resources;
  if (!isRecord(resources)) {
    return { extension, removed: [] };
  }
  const removed = Object.keys(resources).filter((id) => !ids.has(id));
  if (removed.length === 0) {
    return { extension, removed };
  }
  const kept = Object.fromEntries(
    Object.entries(resources).filter(([id]) => ids.has(id)),
  );
  return { extension: { ...extension, resources: kept }, removed };
};

// The V3 rule (native_color.is_native): a background or sprite sheet is in
// native colours if its own ngpc_color says "native", or says nothing and the
// project default (ngpc.default_color) is "native".
export const isNgpcNativeResource = (
  extension: NgpcProjectExtension | null,
  resourceId: string | undefined,
): boolean => {
  if (!resourceId) {
    return false;
  }
  const own = getNgpcResourceSetting(extension, resourceId, "ngpc_color");
  if (own === "native" || own === "gbs") {
    return own === "native";
  }
  return getNgpcProjectSettings(extension).default_color === "native";
};

// What to store in a resource's ngpc_color for a choice: nothing when it is
// the project default, else "native" or "gbs".
export const ngpcColorValue = (
  extension: NgpcProjectExtension | null,
  native: boolean,
): "native" | "gbs" | undefined => {
  const byDefault = getNgpcProjectSettings(extension).default_color === "native";
  if (native === byDefault) {
    return undefined;
  }
  return native ? "native" : "gbs";
};

// Returns a new extension with one project setting set, or removed when
// value is undefined (the V3 then applies its default).
export const setNgpcProjectSetting = <K extends NgpcProjectSettingKey>(
  extension: NgpcProjectExtension | null,
  key: K,
  value: NgpcProjectSettings[K] | undefined,
): NgpcProjectExtension => {
  const base: NgpcProjectExtension = extension ?? {
    schema: NGPC_PROJECT_SCHEMA,
  };
  const ngpc: Record<string, unknown> = isRecord(base.ngpc)
    ? { ...base.ngpc }
    : {};
  if (value === undefined) {
    delete ngpc[key];
  } else {
    ngpc[key] = value;
  }
  return { ...base, ngpc };
};

// True when the extension carries anything; an empty one is not written.
export const hasNgpcContent = (
  extension: NgpcProjectExtension | null,
): boolean => {
  if (!extension) {
    return false;
  }
  const ngpc = extension.ngpc;
  const resources = extension.resources;
  return (
    (isRecord(ngpc) && Object.keys(ngpc).length > 0) ||
    (isRecord(resources) && Object.keys(resources).length > 0)
  );
};

// Native music binding of a GB Studio music resource (V3 FORMAT_PROJET.md,
// "Liaison musique native"). Paths are relative to the project, with "/".
export interface NgpcNativeMusicNgps {
  format: "ngps";
  song: string;
  bank: string;
  note_encoding: "sound-creator" | "midi";
  tick_clock: "vblank";
}

export interface NgpcNativeMusicHeldScore {
  format: "held-score";
  score: string;
  bank: string;
}

export type NgpcNativeMusic = NgpcNativeMusicNgps | NgpcNativeMusicHeldScore;

export const getNgpcResourceSetting = (
  extension: NgpcProjectExtension | null,
  resourceId: string,
  key: string,
): unknown => {
  const resources = extension?.resources;
  if (!isRecord(resources)) {
    return undefined;
  }
  const entry = resources[resourceId];
  return isRecord(entry) ? entry[key] : undefined;
};

// Returns a new extension with one resource key set, or removed when value
// is undefined (an emptied resource entry is removed too).
export const setNgpcResourceSetting = (
  extension: NgpcProjectExtension | null,
  resourceId: string,
  key: string,
  value: unknown,
): NgpcProjectExtension => {
  const base: NgpcProjectExtension = extension ?? {
    schema: NGPC_PROJECT_SCHEMA,
  };
  const resources: Record<string, unknown> = isRecord(base.resources)
    ? { ...base.resources }
    : {};
  const entry: Record<string, unknown> = isRecord(resources[resourceId])
    ? { ...(resources[resourceId] as Record<string, unknown>) }
    : {};
  if (value === undefined) {
    delete entry[key];
  } else {
    entry[key] = value;
  }
  if (Object.keys(entry).length > 0) {
    resources[resourceId] = entry;
  } else {
    delete resources[resourceId];
  }
  return { ...base, resources };
};

// Result of linking a song from the editor (main process dialog).
// .uge converted by the V3 into native-audio/<song>/ (lib/ngpcraft/convertUge).
export type ConvertUgeResult =
  | {
      binding: NgpcNativeMusicHeldScore;
      // Project folder holding the conversion and its loss report.
      folder: string;
      // Octaves added per channel (semitones), when notes were out of range.
      transposeSemitones: number[];
    }
  | { error: string };

// Native sound effects (V3 project_sfx.py): ngpc.native_sfx["<sound id>:<effect>"]
// = timeline steps, written by `convert-sfx` (lib/ngpcraft/convertSfx).
export interface NgpcNativeSfxStep {
  frames: number;
  tone_divider: number;
  tone_attenuation: number;
  noise: number;
  noise_attenuation: number;
}

export type ConvertSfxResult =
  | { timelines: NgpcNativeSfxStep[][]; losses: string[] }
  | { error: string };

const sfxKeyPrefix = (soundId: string) => `${soundId}:`;

// Effects of one sound bound in ngpc.native_sfx, by effect index.
export const getNgpcSoundEffects = (
  extension: NgpcProjectExtension | null,
  soundId: string,
): number[] => {
  const ngpc = extension?.ngpc;
  const table = isRecord(ngpc) ? ngpc.native_sfx : undefined;
  if (!isRecord(table)) {
    return [];
  }
  return Object.keys(table)
    .filter((key) => key.startsWith(sfxKeyPrefix(soundId)))
    .map((key) => Number(key.slice(sfxKeyPrefix(soundId).length)))
    .filter((n) => Number.isInteger(n))
    .sort((a, b) => a - b);
};

// Returns a new extension where every "<sound id>:*" binding is replaced by
// `timelines` (index = effect), or removed when timelines is undefined.
export const setNgpcSoundEffects = (
  extension: NgpcProjectExtension | null,
  soundId: string,
  timelines: NgpcNativeSfxStep[][] | undefined,
): NgpcProjectExtension => {
  const base: NgpcProjectExtension = extension ?? {
    schema: NGPC_PROJECT_SCHEMA,
  };
  const ngpc: Record<string, unknown> = isRecord(base.ngpc)
    ? { ...base.ngpc }
    : {};
  const table: Record<string, unknown> = Object.fromEntries(
    Object.entries(isRecord(ngpc.native_sfx) ? ngpc.native_sfx : {}).filter(
      ([key]) => !key.startsWith(sfxKeyPrefix(soundId)),
    ),
  );
  (timelines ?? []).forEach((rows, effect) => {
    table[`${sfxKeyPrefix(soundId)}${effect}`] = rows;
  });
  if (Object.keys(table).length > 0) {
    // eslint-disable-next-line camelcase -- key of ngpcraft.json (V3 format)
    ngpc.native_sfx = table;
  } else {
    delete ngpc.native_sfx;
  }
  return { ...base, ngpc };
};

export type ChooseNativeSongResult =
  | { binding: NgpcNativeMusicNgps }
  | { error: string }
  | { cancelled: true };
