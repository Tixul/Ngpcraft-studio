/*
 * NgpCraft: the scene sidebar warns when GB Studio's colour modes merge
 * different colours of a background PNG (shades read from green only).
 */
import { findColorLosses, gbShade } from "shared/lib/ngpcraft/colorLoss";

const pixels = (...colours: [number, number, number, number?][]) =>
  new Uint8ClampedArray(
    colours.flatMap(([r, g, b, a]) => [r, g, b, a ?? 255]),
  );

test("shades follow GB Studio's green thresholds", () => {
  expect([0, 64, 65, 129, 130, 204, 205, 255].map(gbShade)).toEqual([
    3, 3, 2, 2, 1, 1, 0, 0,
  ]);
});

test("a red, black and yellow logo: red and black become one shade", () => {
  const losses = findColorLosses(
    pixels([0, 0, 0], [255, 0, 0], [251, 242, 54], [0, 0, 0]),
  );
  expect(losses).toEqual([{ a: "000000", b: "ff0000", shade: 3 }]);
});

test("GB Studio's own shades, near colours and transparency: no warning", () => {
  // The four default GB Studio greens: one colour per shade.
  expect(
    findColorLosses(
      pixels([224, 248, 207], [134, 192, 108], [48, 104, 80], [7, 24, 33]),
    ),
  ).toEqual([]);
  // The same black drawn twice slightly differently.
  expect(findColorLosses(pixels([0, 0, 0], [10, 8, 12]))).toEqual([]);
  // A transparent red pixel is not drawn.
  expect(findColorLosses(pixels([0, 0, 0], [255, 0, 0, 0]))).toEqual([]);
});
