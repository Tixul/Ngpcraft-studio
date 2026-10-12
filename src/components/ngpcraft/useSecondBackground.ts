/* eslint-disable camelcase */ // Keys of ngpcraft.json (V3 format).
/*
 * NgpCraft: a scene's second background (NGPC planes, advanced mode), stored
 * in ngpcraft.json resources.<scene id>.second_background (V3
 * docs/FORMAT_PROJET.md "Second décor NGPC"). Absent = GB Studio mode.
 */
import { useAppSelector } from "store/hooks";
import { getNgpcResourceSetting } from "shared/lib/ngpcraft/projectExtension";
import { isRate } from "shared/lib/ngpcraft/secondBackground";

export const NGPC_SECOND_BACKGROUND_KEY = "second_background";

export interface SecondBackgroundSetting {
  background_id: string;
  scroll_x: string;
  scroll_y: string;
  loop_x?: boolean;
  loop_y?: boolean;
  // V3 second_stream.py: behind the main background (SCR2 in front).
  behind?: boolean;
}

export const isSecondBackgroundSetting = (
  value: unknown,
): value is SecondBackgroundSetting =>
  typeof value === "object" &&
  value !== null &&
  typeof (value as SecondBackgroundSetting).background_id === "string" &&
  isRate((value as SecondBackgroundSetting).scroll_x) &&
  isRate((value as SecondBackgroundSetting).scroll_y);

/** The scene's second background setting, or undefined (GB Studio mode). */
export const useSecondBackground = (sceneId: string) => {
  const value = useAppSelector((state) =>
    getNgpcResourceSetting(
      state.ngpcraft.extension,
      sceneId,
      NGPC_SECOND_BACKGROUND_KEY,
    ),
  );
  return isSecondBackgroundSetting(value) ? value : undefined;
};
