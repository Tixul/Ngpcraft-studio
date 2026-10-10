/* eslint-disable camelcase */ // Keys of the V3 file format.
/*
 * NgpCraft: colours of images by default (ngpcraft.json ngpc.default_color),
 * overridden per resource by ngpc_color "native" / "gbs" — the V3 rule
 * native_color.is_native.
 */
import {
  isNgpcNativeResource,
  ngpcColorValue,
  NgpcProjectExtension,
} from "shared/lib/ngpcraft/projectExtension";

const ext = (
  defaultColor: "native" | undefined,
  own: Record<string, string>,
): NgpcProjectExtension => ({
  schema: "ngpcraft-project/1",
  ngpc: defaultColor ? { default_color: defaultColor } : {},
  resources: Object.fromEntries(
    Object.entries(own).map(([id, v]) => [id, { ngpc_color: v }]),
  ),
});

test("own colour wins, else the project default", () => {
  const byDefault = ext("native", { gb: "gbs" });
  expect(isNgpcNativeResource(byDefault, "any")).toBe(true);
  expect(isNgpcNativeResource(byDefault, "gb")).toBe(false);
  const gbDefault = ext(undefined, { nat: "native" });
  expect(isNgpcNativeResource(gbDefault, "any")).toBe(false);
  expect(isNgpcNativeResource(gbDefault, "nat")).toBe(true);
  expect(isNgpcNativeResource(null, "any")).toBe(false);
  expect(isNgpcNativeResource(byDefault, undefined)).toBe(false);
});

test("a choice equal to the project default stores nothing", () => {
  expect(ngpcColorValue(ext("native", {}), true)).toBeUndefined();
  expect(ngpcColorValue(ext("native", {}), false)).toBe("gbs");
  expect(ngpcColorValue(ext(undefined, {}), true)).toBe("native");
  expect(ngpcColorValue(ext(undefined, {}), false)).toBeUndefined();
});
