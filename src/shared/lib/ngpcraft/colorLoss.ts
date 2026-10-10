/*
 * NgpCraft: colours of a PNG that GB Studio's own colour modes merge. GB
 * Studio reads a background as 4 shades from the GREEN channel only
 * (ColorizedImage.worker indexColour), as drawn for the Game Boy: two colours
 * with the same green level become the same shade (red #FF0000 and black
 * #000000 both have green 0). The "NGPC native" mode keeps the PNG colours.
 */

// GB Studio's shade of a pixel (ColorizedImage.worker.ts indexColour).
export const gbShade = (green: number): number => {
  if (green < 65) {
    return 3;
  }
  if (green < 130) {
    return 2;
  }
  if (green < 205) {
    return 1;
  }
  return 0;
};

// Colours closer than this (sum of channel differences) are the same colour
// drawn slightly differently, not a loss.
const SAME_COLOUR = 48;

export interface ColorLoss {
  // Two colours of the image that become the same GB Studio shade.
  a: string;
  b: string;
  shade: number;
}

const hex = (r: number, g: number, b: number) =>
  [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");

/** The colour pairs merged by GB Studio's shades, most different first. */
export const findColorLosses = (rgba: Uint8ClampedArray): ColorLoss[] => {
  const byShade = new Map<number, Map<string, [number, number, number]>>();
  for (let i = 0; i < rgba.length; i += 4) {
    if (rgba[i + 3] < 128) {
      continue;
    }
    const r = rgba[i];
    const g = rgba[i + 1];
    const b = rgba[i + 2];
    const shade = gbShade(g);
    let colours = byShade.get(shade);
    if (!colours) {
      colours = new Map();
      byShade.set(shade, colours);
    }
    colours.set(hex(r, g, b), [r, g, b]);
  }
  const losses: (ColorLoss & { distance: number })[] = [];
  byShade.forEach((colours, shade) => {
    const list = Array.from(colours.entries());
    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        const [a, ca] = list[i];
        const [b, cb] = list[j];
        const distance =
          Math.abs(ca[0] - cb[0]) +
          Math.abs(ca[1] - cb[1]) +
          Math.abs(ca[2] - cb[2]);
        if (distance > SAME_COLOUR) {
          losses.push({ a, b, shade, distance });
        }
      }
    }
  });
  return losses
    .sort((x, y) => y.distance - x.distance)
    .map(({ a, b, shade }) => ({ a, b, shade }));
};
