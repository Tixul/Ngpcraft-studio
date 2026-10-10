/* eslint-disable camelcase */ // Keys of the V3 native effect format.
/*
 * NgpCraft: sound effects converted by the V3 (`convert-sfx`) and bound in
 * ngpcraft.json ngpc.native_sfx["<sound id>:<effect>"].
 */
import Path from "path";
import { existsSync } from "fs";
import { convertSfx } from "lib/ngpcraft/convertSfx";
import { ngpcraftDefaultV3Root, projectTemplatesRoot } from "consts";
import {
  getNgpcSoundEffects,
  setNgpcSoundEffects,
} from "shared/lib/ngpcraft/projectExtension";

const step = (frames: number) => ({
  frames,
  tone_divider: 100,
  tone_attenuation: 0,
  noise: 0,
  noise_attenuation: 15,
});

test("one sound's effects are replaced together, others kept", () => {
  const original = {
    schema: "ngpcraft-project/1",
    ngpc: {
      frame_rate: 30,
      native_sfx: { "beep:4:0.5": [step(1)], "bank:7": [step(2)] },
    },
  };
  const converted = setNgpcSoundEffects(original, "bank", [
    [step(3)],
    [step(4)],
  ]);
  const table = (converted.ngpc as Record<string, Record<string, unknown>>)
    .native_sfx;
  expect(Object.keys(table).sort()).toEqual(["bank:0", "bank:1", "beep:4:0.5"]);
  expect(getNgpcSoundEffects(converted, "bank")).toEqual([0, 1]);
  expect((converted.ngpc as Record<string, unknown>).frame_rate).toBe(30);

  const removed = setNgpcSoundEffects(converted, "bank", undefined);
  expect(getNgpcSoundEffects(removed, "bank")).toEqual([]);
  const empty = setNgpcSoundEffects(
    { schema: "ngpcraft-project/1", ngpc: { native_sfx: { "x:0": [] } } },
    "x",
    undefined,
  );
  expect(empty.ngpc).toEqual({});
});

const bank = Path.join(
  projectTemplatesRoot,
  "gbs2/assets/sounds/Tronimal_Sound_Effects.sav",
);

(existsSync(bank) ? test : test.skip)(
  "the V3 converts every effect of the gbs2 FX Hammer bank",
  async () => {
    process.env.NGPCRAFT_V3_HOME =
      process.env.NGPCRAFT_V3_HOME || ngpcraftDefaultV3Root;
    const result = await convertSfx(bank, 10);
    expect("timelines" in result).toBe(true);
    if ("timelines" in result) {
      expect(result.timelines).toHaveLength(10);
      expect(result.timelines[0][0].frames).toBeGreaterThan(0);
    }
  },
  120000,
);
