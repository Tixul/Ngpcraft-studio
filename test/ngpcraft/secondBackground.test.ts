/*
 * NgpCraft: the scene view draws the second background (SCR1) for a camera,
 * as the console shows it (V3 second_stream.py / second_background.py: main
 * scroll clamped, then the rate, then clamped to the second map or wrapped
 * when the axis loops).
 */
import {
  clampCamera,
  isRate,
  rateOffset,
  secondBackgroundForCamera,
  secondBackgroundScreens,
  secondOffset,
} from "shared/lib/ngpcraft/secondBackground";

const config = (rateX: string, rateY: string, loopX = false, loopY = false) => ({
  rateX,
  rateY,
  loopX,
  loopY,
});

describe("rates", () => {
  test("fixed, 1, fractions and N% follow the V3", () => {
    expect(rateOffset("fixed", 320)).toBe(0);
    expect(rateOffset("1", 320)).toBe(320);
    expect(rateOffset("1/2", 321)).toBe(160);
    expect(rateOffset("75%", 321)).toBe(240);
    expect(rateOffset("150%", 101)).toBe(151);
    for (let p = 0; p <= 1880; p += 7) {
      expect(rateOffset("50%", p)).toBe(rateOffset("1/2", p));
    }
  });

  test("only the V3's rate strings are accepted", () => {
    for (const ok of ["fixed", "1", "1/256", "0%", "75%", "400%"]) {
      expect(isRate(ok)).toBe(true);
    }
    for (const bad of ["401%", "07%", "5.5%", "%", "1/3", "2", 50]) {
      expect(isRate(bad)).toBe(false);
    }
  });

  test("offset clamped to the second map, unless the axis loops", () => {
    expect(secondOffset(320, "1/2", 240, 160)).toBe(80);
    expect(secondOffset(320, "1/2", 240, 160, true)).toBe(160);
  });
});

describe("secondBackgroundScreens (no camera window)", () => {
  test("rate 1: the image origin stays at the scene origin", () => {
    const screens = secondBackgroundScreens(200, 152, 200, 152, config("1", "1"));
    expect(screens).toEqual([
      { dx: 0, dy: 0, w: 160, h: 152, tx: 0, ty: 0 },
      { dx: 160, dy: 0, w: 40, h: 152, tx: 0, ty: 0 },
    ]);
  });

  test("fixed shows the same window on every screen", () => {
    const screens = secondBackgroundScreens(
      320,
      304,
      160,
      152,
      config("fixed", "fixed"),
    );
    expect(screens.map((s) => [s.dx - s.tx, s.dy - s.ty])).toEqual([
      [0, 0],
      [0, 0],
      [0, 0],
      [0, 0],
    ]);
  });

  test("a 144 px scene uses the GB screen height", () => {
    const screens = secondBackgroundScreens(160, 144, 160, 144, config("1", "1"));
    expect(screens).toEqual([{ dx: 0, dy: 0, w: 160, h: 144, tx: 0, ty: 0 }]);
  });

  test("a map smaller than the view is refused unless that axis loops", () => {
    expect(
      secondBackgroundScreens(320, 152, 64, 48, config("1", "1")),
    ).toEqual([]);
    expect(
      secondBackgroundScreens(320, 152, 64, 48, config("1", "1", true, true)),
    ).toHaveLength(2);
    expect(
      secondBackgroundScreens(320, 152, 160, 152, config("2", "1")),
    ).toEqual([]);
  });
});

describe("secondBackgroundForCamera (camera window)", () => {
  test("one transform for the whole scene, camera clamped first", () => {
    // Scene 640 x 304, camera asked at (1000, 200) -> clamped (480, 152).
    expect(clampCamera(1000, 200, 640, 304)).toEqual({ x: 480, y: 152 });
    const [s] = secondBackgroundForCamera(
      640,
      304,
      400,
      400,
      config("1/2", "75%"),
      1000,
      200,
    );
    // x: 480 >> 1 = 240 (fits 400 - 160); y: 152 * 75 / 100 = 114.
    expect(s).toEqual({ dx: 0, dy: 0, w: 640, h: 304, tx: 240, ty: 38 });
  });

  test("a looping axis is not clamped to the second map", () => {
    const [s] = secondBackgroundForCamera(
      2040,
      152,
      64,
      48,
      config("150%", "fixed", true, true),
      1000,
      0,
    );
    expect(s.tx).toBe(1000 - 1500);
    expect(s.ty).toBe(0);
  });
});
