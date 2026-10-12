/*
 * NgpCraft: what the V3 used for one scene at the last build, read from its
 * export report (scene_engine.scenes). Shown in the scene sidebar.
 */

// Tile slots shared by backgrounds and sprites (512 tiles of 8×8).
export const NGPC_TILE_SLOTS = 512;
// Hardware palettes of a background plane.
export const NGPC_BACKGROUND_PALETTES = 16;

export interface NgpcSceneBudget {
  // GB Studio scene id.
  id: string;
  name: string;
  usedTiles: number;
  backgroundTiles: number;
  spriteTiles: number;
  backgroundPalettes: number;
  actors: number;
  // Projectile pool of a scene that launches projectiles (ngpc.projectile_capacity).
  projectiles?: { capacity: number; ramBytes: number };
  // Height of the shown view: 152, or 144 for a scene drawn between bands.
  viewHeight: number;
  // Warnings of the V3 for this scene, in English.
  warnings: string[];
}

// V3 `budget` (ngpcraft-v3/ngpcraft/scene_budget.py): an estimate of one
// scene from the saved project, computed even when the build would refuse it.
export interface NgpcBudgetEstimate {
  sceneId: string;
  usedTiles: number;
  backgroundTiles: number;
  secondBackgroundTiles: number;
  spriteTiles: number;
  textReserve: number;
  backgroundPalettes: number;
  over: boolean;
  // Heaviest sprite sheets first.
  sheets: { name: string; tiles: number }[];
  errors: string[];
}
