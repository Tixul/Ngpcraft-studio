/* eslint-disable camelcase */ // Keys of the V3 palette-plan output.
/*
 * NgpCraft: the editor only shows a palette plan that matches the V3
 * contract ngpcraft-palette-plan/1 (docs/ETUDE_16_PALETTES.md §7).
 */
import {
  parsePalettePlan,
  parseSpritePalettePlan,
} from "shared/lib/ngpcraft/palettePlan";

const valid = {
  format: "ngpcraft-palette-plan/1",
  background: "bg-1",
  width_tiles: 2,
  height_tiles: 2,
  backdrop: "e0f0e0",
  palettes: [["206020", "40a040", "f0f0f0"], ["0000f0"]],
  tile_palettes: [0, 0, 1, -1],
  errors: [{ x: 1, y: 1, colors: ["ff0000", "00ff00", "0000ff", "ffff00"] }],
  palette_limit: 16,
};

test("a plan following the contract is read", () => {
  const plan = parsePalettePlan(valid);
  expect(plan).toBeDefined();
  expect(plan?.widthTiles).toBe(2);
  expect(plan?.palettes).toHaveLength(2);
  expect(plan?.errors[0]).toEqual({
    x: 1,
    y: 1,
    colors: ["ff0000", "00ff00", "0000ff", "ffff00"],
  });
});

test("anything outside the contract is rejected", () => {
  expect(parsePalettePlan({ ...valid, format: "other/1" })).toBeUndefined();
  // One palette index per tile.
  expect(
    parsePalettePlan({ ...valid, tile_palettes: [0, 0, 1] }),
  ).toBeUndefined();
  // Index beyond the palettes.
  expect(
    parsePalettePlan({ ...valid, tile_palettes: [0, 0, 2, -1] }),
  ).toBeUndefined();
  // A palette has 1 to 3 colours, lowercase rrggbb.
  expect(
    parsePalettePlan({ ...valid, palettes: [["a", "b", "c", "d"]] }),
  ).toBeUndefined();
  expect(parsePalettePlan({ ...valid, backdrop: "E0F0E0" })).toBeUndefined();
  // More palettes than the limit, without recycling or its failure.
  expect(parsePalettePlan({ ...valid, palette_limit: 1 })).toBeUndefined();
  expect(parsePalettePlan(null)).toBeUndefined();
});

test("over the limit: recycled slots, or where recycling fails", () => {
  // V3 palette_recycle.py: slots shared by palettes never on screen together.
  const recycled = parsePalettePlan({
    ...valid,
    palette_limit: 1,
    recycle: { palettes: 2, slots: 1 },
  });
  expect(recycled?.recycle).toEqual({ palettes: 2, slots: 1 });
  const failed = parsePalettePlan({
    ...valid,
    palette_limit: 1,
    errors: [{ x: 8, y: 0, colors: [], reason: "on_screen" }],
  });
  expect(failed?.errors[0]).toEqual({
    x: 8,
    y: 0,
    colors: [],
    reason: "on_screen",
  });
});

test("the output of the real V3 palette-plan is read", () => {
  // ngpcraft-v3: tools/lab_native_color.py picture, tile (2, 1) with a 4th
  // colour; produced by `python -m ngpcraft palette-plan`.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const output = require("./fixtures/v3PalettePlan.json");
  const plan = parsePalettePlan(output);
  expect(plan).toBeDefined();
  expect(plan?.palettes).toHaveLength(16);
  expect(plan?.errors).toEqual([
    { x: 2, y: 1, colors: expect.arrayContaining(["ffffff"]) },
  ]);
});

test("the real V3 sprite plan (palette-plan --sprite) is read", () => {
  // ngpcraft-v3: tools/lab_native_sprites.py sheet (4 quarters of 3 colours).
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const output = require("./fixtures/v3SpritePalettePlan.json");
  const plan = parseSpritePalettePlan(output);
  expect(plan?.palettes).toHaveLength(4);
  expect(plan?.paletteLimit).toBe(8);
  expect(
    parseSpritePalettePlan({ ...output, palette_limit: 2 }),
  ).toBeUndefined();
  expect(
    parseSpritePalettePlan({ ...output, errors: [{ tile: 1 }] }),
  ).toBeUndefined();
});
