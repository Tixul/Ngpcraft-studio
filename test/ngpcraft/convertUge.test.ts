/*
 * NgpCraft: "Convert for the NGPC" on a .uge song. Same recipe as the gbs2
 * demo built by the V3 (2026-10-07, played on console): Gona Space needs its
 * wave channel two octaves up and gives that demo's held score (SHA-256).
 *
 * Needs the V3 compiler and Python; skipped otherwise.
 */
import Path from "path";
import os from "os";
import { createHash } from "crypto";
import { copySync, existsSync, mkdtempSync, readFileSync } from "fs-extra";
import { convertUge, nativeAudioFolderName } from "lib/ngpcraft/convertUge";
import { ngpcraftDefaultV3Root, projectTemplatesRoot } from "consts";

const v3Root = process.env.NGPCRAFT_V3_HOME || ngpcraftDefaultV3Root;
const available = existsSync(Path.join(v3Root, "ngpcraft"));
const template = Path.join(projectTemplatesRoot, "gbs2");

// held-score.json of the V3 gbs2 demo for Rulz_GonaSpace.uge.
const GONA_SPACE_HELD_SCORE_SHA256 =
  "2fb875a3c5aaa10d91af9a77a080ca48ada2f797f303850933d57e6049b716aa";

const sha256 = (path: string) =>
  createHash("sha256").update(readFileSync(path)).digest("hex");

test("song folder names stay readable and valid", () => {
  expect(nativeAudioFolderName("assets/music/Rulz_Into the woods.uge")).toBe(
    "Rulz_Into the woods",
  );
  expect(nativeAudioFolderName("x<y?.uge")).toBe("x_y_");
});

(available ? test : test.skip)(
  "a .uge converts like the V3 gbs2 demo",
  async () => {
    const project = mkdtempSync(Path.join(os.tmpdir(), "ngpcraft-uge-"));
    copySync(
      Path.join(template, "assets", "music", "Rulz_GonaSpace.uge"),
      Path.join(project, "assets", "music", "Rulz_GonaSpace.uge"),
    );
    const result = await convertUge(
      project,
      "assets/music/Rulz_GonaSpace.uge",
    );
    if ("error" in result) {
      throw new Error(result.error);
    }
    expect(result.transposeSemitones).toEqual([0, 0, 24]);
    expect(result.binding).toEqual({
      format: "held-score",
      score: "native-audio/Rulz_GonaSpace/held-score.json",
      bank: "native-audio/Rulz_GonaSpace/bank.json",
    });
    expect(sha256(Path.join(project, result.binding.score))).toBe(
      GONA_SPACE_HELD_SCORE_SHA256,
    );
    for (const file of ["loss-report.json", "song.ngps", "policy.json"]) {
      expect(existsSync(Path.join(project, result.folder, file))).toBe(true);
    }
  },
  300000,
);
