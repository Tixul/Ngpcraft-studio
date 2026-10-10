export const maxSpriteTilesForBackgroundTilesLength = (
  backgroundTilesLength: number,
  isCGBOnly: boolean,
  spriteMode: "8x8" | "8x16",
) => {
  const max8x16SpriteTiles = (): number => {
    if (isCGBOnly) {
      if (backgroundTilesLength <= 256) {
        return 192;
      }
      if (backgroundTilesLength * 0.5 < 192) {
        return 192 - Math.ceil((backgroundTilesLength / 2 - 128) / 2) * 2;
      }
      return 128;
    }
    if (backgroundTilesLength <= 128) {
      return 96;
    }
    if (backgroundTilesLength < 192) {
      return 96 - Math.ceil((backgroundTilesLength - 128) / 2);
    }
    return 64;
  };
  const baseTileCount = max8x16SpriteTiles();
  return spriteMode === "8x16" ? baseTileCount : baseTileCount * 2;
};

// NgpCraft: Neo Geo Pocket Color character RAM holds 512 tiles of 8x8,
// shared by backgrounds and sprites (no Game Boy VRAM split). Estimate only:
// text and UI frame tiles are not counted; the V3 build reports the exact
// budget. Returned in the scene's sprite mode units (8x16 = two 8x8 tiles).
export const NGPC_CHARACTER_TILES = 512;

export const ngpcMaxSpriteTiles = (
  backgroundTilesLength: number,
  spriteMode: "8x8" | "8x16",
) => {
  const free = Math.max(0, NGPC_CHARACTER_TILES - backgroundTilesLength);
  return spriteMode === "8x16" ? Math.floor(free / 2) : free;
};
