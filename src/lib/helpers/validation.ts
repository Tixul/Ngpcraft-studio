import { compileImage } from "lib/compiler/compileImages";
import {
  Background,
  ColorCorrectionSetting,
  ColorModeSetting,
  Palette,
  TilesetAsset,
} from "shared/lib/resources/types";
import { HexPalette } from "shared/lib/tiles/autoColor";
import l10n from "shared/lib/lang/l10n";
import {
  MAX_BACKGROUND_TILES,
  MAX_BACKGROUND_TILES_CGB,
  SCREEN_HEIGHT_PX,
} from "consts";
import { NGPC_CHARACTER_TILES } from "shared/lib/helpers/sprites";

// NgpCraft: the editor checks backgrounds with GB Studio's image compiler,
// which warns with Game Boy limits (192 / 384 unique tiles). Those warnings
// are replaced by the NGPC ones: 512 tiles shared by background and sprites,
// and the 152 px screen (a 144 px background is shown with black bands).
const ngpcBackgroundWarnings = (
  warnings: string[],
  tilesetLength: number,
  imageHeight: number,
  is360: boolean,
): string[] => {
  const gbTileWarnings = [MAX_BACKGROUND_TILES, MAX_BACKGROUND_TILES_CGB].map(
    (maxTilesetLength) =>
      l10n("WARNING_BACKGROUND_TOO_MANY_TILES", {
        tilesetLength,
        maxTilesetLength,
      }),
  );
  const result = warnings.filter((w) => !gbTileWarnings.includes(w));
  if (!is360 && tilesetLength > NGPC_CHARACTER_TILES) {
    result.push(
      l10n("WARNING_BACKGROUND_TOO_MANY_TILES", {
        tilesetLength,
        maxTilesetLength: NGPC_CHARACTER_TILES,
      }),
    );
  }
  if (!is360 && imageHeight >= 144 && imageHeight < SCREEN_HEIGHT_PX) {
    result.push(l10n("WARNING_NGPC_BACKGROUND_BANDS", { height: imageHeight }));
  }
  return result;
};

export interface BackgroundInfo {
  numTiles: number;
  warnings: string[];
  lookup: number[];
  autoPalettes?: Palette[];
  attr: number[];
}

export const getBackgroundInfo = async (
  background: Background,
  commonTileset: TilesetAsset | undefined,
  is360: boolean,
  uiPalette: HexPalette | undefined,
  colorMode: ColorModeSetting,
  colorCorrection: ColorCorrectionSetting,
  autoTileFlipEnabled: boolean,
  projectPath: string,
): Promise<BackgroundInfo> => {
  const warnings: string[] = [];
  try {
    const result = await compileImage(
      background,
      commonTileset,
      is360,
      uiPalette,
      colorMode,
      colorCorrection,
      autoTileFlipEnabled,
      projectPath,
      { warnings: (msg) => warnings.push(msg) },
    );

    return {
      warnings: ngpcBackgroundWarnings(
        warnings,
        result.tilesetLength,
        background.imageHeight,
        is360,
      ),
      numTiles: result.tilesetLength,
      lookup: result.tilemap,
      autoPalettes: result.autoPalettes,
      attr: result.attr,
    };
  } catch (e) {
    warnings.push(String(e));
    return {
      warnings,
      numTiles: 0,
      lookup: [],
      autoPalettes: [],
      attr: [],
    };
  }
};
