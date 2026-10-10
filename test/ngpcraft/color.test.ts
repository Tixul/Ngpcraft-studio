/*
 * NgpCraft F6: NGPC colours follow the V3 rule (scene_assets.py rgb444:
 * each 8-bit channel >> 4), shown on screen as c * 17.
 */
import {
  hex2NgpcRgb,
  ngpc4ToHex,
  rawHexToNgpc4,
  rawHexToNgpcHex,
  rgb8ToNgpc4,
} from "shared/lib/ngpcraft/color";

test("stored colours compile to their 4 high bits per channel", () => {
  // GB Studio default palette (consts DMG_PALETTE).
  expect(rawHexToNgpc4("E8F8E0")).toEqual({ r: 14, g: 15, b: 14 });
  expect(rawHexToNgpc4("B0F088")).toEqual({ r: 11, g: 15, b: 8 });
  expect(rawHexToNgpc4("509878")).toEqual({ r: 5, g: 9, b: 7 });
  expect(rawHexToNgpc4("202850")).toEqual({ r: 2, g: 2, b: 5 });
  // Values below 0x10 vanish: no 5-bit nuance survives.
  expect(rawHexToNgpc4("080808")).toEqual({ r: 0, g: 0, b: 0 });
});

test("NGPC hex round-trips through the V3 rule", () => {
  for (let c = 0; c <= 15; c++) {
    const hex = ngpc4ToHex(c, 15 - c, c);
    expect(rawHexToNgpc4(hex)).toEqual({ r: c, g: 15 - c, b: c });
    expect(rawHexToNgpcHex(hex)).toBe(hex);
  }
  expect(rawHexToNgpcHex("E8F8E0")).toBe("eeffee");
  expect(hex2NgpcRgb("E8F8E0")).toEqual({ r: 238, g: 255, b: 238 });
});

test("8-bit RGB snaps to the nearest 4-bit channel", () => {
  expect(rgb8ToNgpc4(255, 0, 128)).toEqual({ r: 15, g: 0, b: 8 });
  expect(rgb8ToNgpc4(300, -5, 17)).toEqual({ r: 15, g: 0, b: 1 });
});
