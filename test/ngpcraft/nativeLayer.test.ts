/*
 * NgpCraft: a native-colour second background in the scene view follows the
 * V3 rule (native_color.native_pixels): 4-bit colours, the most frequent one
 * is the backdrop, transparent on the front plane with alpha < 128 pixels.
 */
import { toNativeFrontLayer } from "shared/lib/ngpcraft/nativeLayer";

const rgba = (...px: number[][]) =>
  new Uint8ClampedArray(px.flatMap(([r, g, b, a]) => [r, g, b, a ?? 255]));

test("the most frequent colour is the backdrop, transparent", () => {
  const data = rgba([0, 0, 0], [0, 0, 0], [255, 0, 0], [251, 242, 54]);
  expect(toNativeFrontLayer(data)).toBe(0x000);
  expect(Array.from(data)).toEqual([
    0, 0, 0, 0, 0, 0, 0, 0, 255, 0, 0, 255, 255, 255, 51, 255,
  ]);
});

test("ties take the smallest RGB444 word; transparent pixels stay out", () => {
  // Red and blue once each; a transparent green that is not counted.
  const data = rgba([255, 0, 0], [0, 0, 255], [0, 255, 0, 0], [0, 255, 0, 0]);
  expect(toNativeFrontLayer(data)).toBe(0x00f);
  expect(data[3]).toBe(255); // red opaque
  expect(data[7]).toBe(0); // blue = backdrop
  expect(data[11]).toBe(0); // transparent
});
