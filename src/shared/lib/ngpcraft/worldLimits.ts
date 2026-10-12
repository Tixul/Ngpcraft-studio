/*
 * NgpCraft: scene size limits of the NGPC (ngpcraft-v3/ngpcraft/world.py,
 * ngpcraft-v3/docs/PLAN_GRANDES_CARTES.md). GB Studio stops at 255 tiles per
 * axis; the NGPC has no world size (the map is streamed into 32×32 planes):
 * 4095 tiles per axis keep a position in pixels within a script variable,
 * 65535 tiles in all keep every map index 16-bit (no per-frame cost).
 * A scene over 255 tiles switches the game to 32-bit positions (+1 % CPU,
 * measured); smaller scenes compile exactly as before.
 */

export const GB_MAX_SCENE_TILES = 255;
export const NGPC_MAX_SCENE_TILES = 4095;
export const NGPC_MAX_SCENE_CELLS = 65535;

// Over the NGPC's limits: the V3 build refuses the scene (scene_build.py).
export const isSceneTooLarge = (width: number, height: number): boolean =>
  width > NGPC_MAX_SCENE_TILES ||
  height > NGPC_MAX_SCENE_TILES ||
  width * height > NGPC_MAX_SCENE_CELLS;
