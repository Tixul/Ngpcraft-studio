/*
 * NgpCraft: Neo Geo Pocket Color colours, 4 bits per channel (4 096 colours).
 *
 * Projects keep GB Studio's storage, "RRGGBB" hex strings. The V3 compiler
 * keeps the 4 high bits of each 8-bit channel (scene_assets.py rgb444:
 * channel >> 4), and the K2GE has no colour correction. The editor shows
 * exactly that colour, expanded back to 8 bits (c * 17) for the screen.
 */
import type { RGB8 } from "shared/lib/helpers/color";

export const NGPC_CHANNEL_MAX = 15;

export interface RGB4 {
  r: number;
  g: number;
  b: number;
}

const clamp4 = (value: number) =>
  Math.min(NGPC_CHANNEL_MAX, Math.max(0, Math.round(value)));

const channel = (hex: string, offset: number) => {
  const value = parseInt(hex.substring(offset, offset + 2), 16);
  return Number.isNaN(value) ? 0 : value;
};

// "RRGGBB" -> 4-bit channels, as the V3 compiles them.
export const rawHexToNgpc4 = (hex: string): RGB4 => ({
  r: channel(hex, 0) >> 4,
  g: channel(hex, 2) >> 4,
  b: channel(hex, 4) >> 4,
});

const byteHex = (c4: number) => (clamp4(c4) * 17).toString(16).padStart(2, "0");

// 4-bit channels -> "rrggbb" hex. Stored as is, it compiles back to the same
// channels ((c * 17) >> 4 = c).
export const ngpc4ToHex = (r: number, g: number, b: number): string =>
  `${byteHex(r)}${byteHex(g)}${byteHex(b)}`;

// Any stored colour -> the colour the NGPC shows, as "rrggbb".
export const rawHexToNgpcHex = (hex: string): string => {
  const { r, g, b } = rawHexToNgpc4(hex);
  return ngpc4ToHex(r, g, b);
};

// Any stored colour -> 8-bit RGB the NGPC shows (for canvases).
export const hex2NgpcRgb = (hex: string): RGB8 => {
  const { r, g, b } = rawHexToNgpc4(hex);
  return { r: r * 17, g: g * 17, b: b * 17 };
};

// 8-bit RGB (e.g. from HSV) -> nearest 4-bit channels.
export const rgb8ToNgpc4 = (r: number, g: number, b: number): RGB4 => ({
  r: clamp4(r / 17),
  g: clamp4(g / 17),
  b: clamp4(b / 17),
});
