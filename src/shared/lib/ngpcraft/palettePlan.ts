/*
 * NgpCraft: "NGPC native" colours of a background (docs/ETUDE_16_PALETTES.md).
 * The V3 groups the PNG colours into NGPC palettes (3 colours + backdrop, at
 * most 16) and reports it with `python -m ngpcraft palette-plan`. This file
 * holds the plan type and a strict validator: an output that does not match
 * the contract is not shown.
 */

export const NGPC_PALETTE_PLAN_FORMAT = "ngpcraft-palette-plan/1";

// ngpcraft.json resources.<background id>.ngpc_color
export const NGPC_COLOR_KEY = "ngpc_color";
export const NGPC_COLOR_NATIVE = "native";
// ngpcraft.json resources.<background id>.ngpc_backdrop: "rrggbb", the
// backdrop of a native background; absent = the most frequent colour.
export const NGPC_BACKDROP_KEY = "ngpc_backdrop";

export interface NgpcPalettePlanError {
  x: number;
  y: number;
  colors: string[];
  reason?: string;
}

export interface NgpcPalettePlan {
  background: string;
  widthTiles: number;
  heightTiles: number;
  backdrop: string;
  palettes: string[][];
  tilePalettes: number[];
  errors: NgpcPalettePlanError[];
  paletteLimit: number;
  // Over the limit: slots shared between palettes never on screen together
  // (V3 palette_recycle.py).
  recycle?: { palettes: number; slots: number };
}

const isHex = (value: unknown): value is string =>
  typeof value === "string" && /^[0-9a-f]{6}$/.test(value);

const isCount = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;

export const parsePalettePlan = (
  data: unknown,
): NgpcPalettePlan | undefined => {
  if (typeof data !== "object" || data === null) {
    return undefined;
  }
  const d = data as Record<string, unknown>;
  if (d.format !== NGPC_PALETTE_PLAN_FORMAT) {
    return undefined;
  }
  const { width_tiles: w, height_tiles: h, palette_limit: limit } = d;
  if (!isCount(w) || !isCount(h) || !isCount(limit)) {
    return undefined;
  }
  if (typeof d.background !== "string" || !isHex(d.backdrop)) {
    return undefined;
  }
  // Over the limit is not an error: palette_recycle.py shares slots (recycle).
  const palettes = d.palettes;
  if (
    !Array.isArray(palettes) ||
    !palettes.every(
      (p) =>
        Array.isArray(p) && p.length >= 1 && p.length <= 3 && p.every(isHex),
    )
  ) {
    return undefined;
  }
  const tiles = d.tile_palettes;
  if (
    !Array.isArray(tiles) ||
    tiles.length !== w * h ||
    !tiles.every(
      (t) =>
        typeof t === "number" &&
        Number.isInteger(t) &&
        t >= -1 &&
        t < palettes.length,
    )
  ) {
    return undefined;
  }
  const errors = d.errors;
  // Over the limit only with the recycling, or the place where it fails.
  if (
    Array.isArray(errors) &&
    palettes.length > limit &&
    !isRecycle(d.recycle) &&
    !errors.some(
      (e) =>
        typeof e === "object" &&
        e !== null &&
        (e as Record<string, unknown>).reason === "on_screen",
    )
  ) {
    return undefined;
  }
  if (
    !Array.isArray(errors) ||
    !errors.every(
      (e) =>
        typeof e === "object" &&
        e !== null &&
        isCount((e as Record<string, unknown>).x) &&
        isCount((e as Record<string, unknown>).y) &&
        Array.isArray((e as Record<string, unknown>).colors) &&
        ((e as Record<string, unknown>).colors as unknown[]).every(isHex),
    )
  ) {
    return undefined;
  }
  return {
    background: d.background,
    widthTiles: w,
    heightTiles: h,
    backdrop: d.backdrop,
    palettes: palettes as string[][],
    tilePalettes: tiles as number[],
    errors: (errors as Record<string, unknown>[]).map((e) => ({
      x: e.x as number,
      y: e.y as number,
      colors: e.colors as string[],
      ...(typeof e.reason === "string" && { reason: e.reason }),
    })),
    paletteLimit: limit,
    ...(isRecycle(d.recycle) && { recycle: d.recycle }),
  };
};

const isRecycle = (
  value: unknown,
): value is { palettes: number; slots: number } =>
  typeof value === "object" &&
  value !== null &&
  isCount((value as Record<string, unknown>).palettes) &&
  isCount((value as Record<string, unknown>).slots);

// Phase 2: a sprite sheet (`palette-plan --sprite`, V3 docs/M7_NATIVE_COLOR.md).
export interface NgpcSpritePalettePlan {
  sprite: string;
  tiles: number;
  palettes: string[][];
  // A tile with more than 3 opaque colours, or too many palettes.
  errors: {
    tile?: number;
    colors?: string[];
    reason?: string;
    palettes?: number;
  }[];
  paletteLimit: number;
}

export const parseSpritePalettePlan = (
  data: unknown,
): NgpcSpritePalettePlan | undefined => {
  if (typeof data !== "object" || data === null) {
    return undefined;
  }
  const d = data as Record<string, unknown>;
  if (
    d.format !== NGPC_PALETTE_PLAN_FORMAT ||
    typeof d.sprite !== "string" ||
    !isCount(d.tiles) ||
    !isCount(d.palette_limit)
  ) {
    return undefined;
  }
  const limit = d.palette_limit;
  const palettes = d.palettes;
  if (
    !Array.isArray(palettes) ||
    palettes.length > limit ||
    !palettes.every(
      (p) =>
        Array.isArray(p) && p.length >= 1 && p.length <= 3 && p.every(isHex),
    )
  ) {
    return undefined;
  }
  const errors = d.errors;
  if (
    !Array.isArray(errors) ||
    !errors.every(
      (e) =>
        typeof e === "object" &&
        e !== null &&
        ((isCount((e as Record<string, unknown>).tile) &&
          Array.isArray((e as Record<string, unknown>).colors) &&
          ((e as Record<string, unknown>).colors as unknown[]).every(isHex)) ||
          (e as Record<string, unknown>).reason === "palette_limit"),
    )
  ) {
    return undefined;
  }
  return {
    sprite: d.sprite,
    tiles: d.tiles,
    palettes: palettes as string[][],
    errors: errors as NgpcSpritePalettePlan["errors"],
    paletteLimit: limit,
  };
};
