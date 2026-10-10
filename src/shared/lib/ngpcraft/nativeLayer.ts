/*
 * NgpCraft: a native-colour image as the second background plane (SCR1)
 * shows it, with the V3 rule (native_color.native_pixels): each channel on 4
 * bits (shown as c * 17), the backdrop is the most frequent 4-bit colour of
 * the opaque pixels (tie: smallest RGB444 word), and the backdrop colour and
 * transparent pixels (alpha < 128) are transparent on the front plane.
 */

const word = (r: number, g: number, b: number) =>
  ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4);

/** RGBA pixels rewritten in place; returns the backdrop RGB444 word. */
export const toNativeFrontLayer = (rgba: Uint8ClampedArray): number => {
  const counts = new Map<number, number>();
  for (let i = 0; i < rgba.length; i += 4) {
    if (rgba[i + 3] >= 128) {
      const w = word(rgba[i], rgba[i + 1], rgba[i + 2]);
      counts.set(w, (counts.get(w) ?? 0) + 1);
    }
  }
  let backdrop = 0;
  let best = -1;
  counts.forEach((n, w) => {
    if (n > best || (n === best && w < backdrop)) {
      best = n;
      backdrop = w;
    }
  });
  for (let i = 0; i < rgba.length; i += 4) {
    const w = word(rgba[i], rgba[i + 1], rgba[i + 2]);
    if (rgba[i + 3] < 128 || w === backdrop) {
      rgba[i + 3] = 0;
      continue;
    }
    rgba[i] = (w >> 8) * 17;
    rgba[i + 1] = ((w >> 4) & 15) * 17;
    rgba[i + 2] = (w & 15) * 17;
    rgba[i + 3] = 255;
  }
  return backdrop;
};
