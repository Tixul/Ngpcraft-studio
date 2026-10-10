/*
 * NgpCraft: where the second background (SCR1) sits for a camera position,
 * with the V3 rule (second_stream.py / second_background.py): main scroll
 * clamped to its map, then the rate ("fixed", "1", "1/2".."1/256", or "N%" =
 * floor(p * N / 100), N in 0..400), then clamped to the second map, or wrapped
 * when the axis loops. A 144 px scene shows the GB screen
 * (background_stream.view_of).
 */
// V3 constants: NGPC screen, and the GB screen shown for a 144 px scene.
export const VIEW_W = 160;
const SCREEN_H = 152;
const GB_SCREEN_H = 144;
export const MAX_PERCENT = 400;

// second_background.py SCROLL_RATES: shift of the camera, null = fixed.
export const RATE_SHIFT: Record<string, number | null> = {
  fixed: null,
  "1": 0,
  "1/2": 1,
  "1/4": 2,
  "1/8": 3,
  "1/16": 4,
  "1/32": 5,
  "1/64": 6,
  "1/128": 7,
  "1/256": 8,
};

/** N for an "N%" rate (second_background.percent_of), else undefined. */
export const percentOf = (rate: string): number | undefined => {
  const match = /^(0|[1-9][0-9]{0,2})%$/.exec(rate);
  if (!match) {
    return undefined;
  }
  const value = Number(match[1]);
  return value <= MAX_PERCENT ? value : undefined;
};

export const isRate = (rate: unknown): rate is string =>
  typeof rate === "string" &&
  (rate in RATE_SHIFT || percentOf(rate) !== undefined);

/** Offset before the second map's own clamp (second_background.rate_offset). */
export const rateOffset = (rate: string, position: number): number => {
  const percent = percentOf(rate);
  if (percent !== undefined) {
    return Math.floor((position * percent) / 100);
  }
  const shift = RATE_SHIFT[rate];
  return shift === null || shift === undefined ? 0 : position >> shift;
};

const clamp = (v: number, min: number, max: number) =>
  Math.max(min, Math.min(max, v));

export const viewHeight = (sceneH: number) =>
  sceneH >= SCREEN_H ? SCREEN_H : GB_SCREEN_H;

/** Clamped main camera, as GB Studio's scroll does. */
export const clampCamera = (
  x: number,
  y: number,
  sceneW: number,
  sceneH: number,
) => ({
  x: clamp(Math.round(x), 0, Math.max(0, sceneW - VIEW_W)),
  y: clamp(Math.round(y), 0, Math.max(0, sceneH - viewHeight(sceneH))),
});

/** Offset of the second map on one axis for a clamped camera position. */
export const secondOffset = (
  camera: number,
  rate: string,
  secondSize: number,
  viewSize: number,
  loop = false,
) => {
  const raw = rateOffset(rate, camera);
  return loop ? raw : clamp(raw, 0, Math.max(0, secondSize - viewSize));
};

export interface SecondBackgroundConfig {
  rateX: string;
  rateY: string;
  loopX: boolean;
  loopY: boolean;
}

export interface SecondScreen {
  // Destination rectangle in the scene (pixels) and where the second
  // image's origin lands in scene pixels for that camera (tx, ty). With a
  // loop the image repeats from that origin, otherwise it is drawn once.
  dx: number;
  dy: number;
  w: number;
  h: number;
  tx: number;
  ty: number;
}

/** False when the V3 refuses the second map (no implicit padding). */
export const secondBackgroundFits = (
  sceneH: number,
  secondW: number,
  secondH: number,
  config: SecondBackgroundConfig,
) =>
  isRate(config.rateX) &&
  isRate(config.rateY) &&
  (config.loopX || secondW >= VIEW_W) &&
  (config.loopY || secondH >= viewHeight(sceneH));

const screenFor = (
  rect: { dx: number; dy: number; w: number; h: number },
  camera: { x: number; y: number },
  secondW: number,
  secondH: number,
  viewH: number,
  config: SecondBackgroundConfig,
): SecondScreen => ({
  ...rect,
  tx:
    camera.x -
    secondOffset(camera.x, config.rateX, secondW, VIEW_W, config.loopX),
  ty:
    camera.y -
    secondOffset(camera.y, config.rateY, secondH, viewH, config.loopY),
});

/** One camera for the whole scene (the editor's camera window). */
export const secondBackgroundForCamera = (
  sceneW: number,
  sceneH: number,
  secondW: number,
  secondH: number,
  config: SecondBackgroundConfig,
  cameraX: number,
  cameraY: number,
): SecondScreen[] => {
  if (!secondBackgroundFits(sceneH, secondW, secondH, config)) {
    return [];
  }
  const camera = clampCamera(cameraX, cameraY, sceneW, sceneH);
  return [
    screenFor(
      { dx: 0, dy: 0, w: sceneW, h: sceneH },
      camera,
      secondW,
      secondH,
      viewHeight(sceneH),
      config,
    ),
  ];
};

/** Each screen of the scene grid, camera on that screen. */
export const secondBackgroundScreens = (
  sceneW: number,
  sceneH: number,
  secondW: number,
  secondH: number,
  config: SecondBackgroundConfig,
): SecondScreen[] => {
  if (!secondBackgroundFits(sceneH, secondW, secondH, config)) {
    return [];
  }
  const viewH = viewHeight(sceneH);
  const screens: SecondScreen[] = [];
  for (let dy = 0; dy < sceneH; dy += viewH) {
    for (let dx = 0; dx < sceneW; dx += VIEW_W) {
      screens.push(
        screenFor(
          {
            dx,
            dy,
            w: Math.min(VIEW_W, sceneW - dx),
            h: Math.min(viewH, sceneH - dy),
          },
          clampCamera(dx, dy, sceneW, sceneH),
          secondW,
          secondH,
          viewH,
          config,
        ),
      );
    }
  }
  return screens;
};
