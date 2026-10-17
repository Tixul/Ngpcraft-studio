/*
 * NgpCraft: check the tools a ROM build needs (Preferences > NgpCraft >
 * Check): the V3 folder, Python 3.10+, the Toshiba toolchain (through Wine
 * off Windows), with the V3's own `check-toolchain` command.
 */
import Path from "path";
import { spawnSync } from "child_process";
import { pathExists } from "fs-extra";
import {
  getPython,
  getToolchainPath,
  getV3Root,
  getWine,
  isWindows,
  v3Env,
} from "lib/ngpcraft/v3Paths";

export interface NgpcToolsReport {
  ok: boolean;
  lines: string[];
}

export const checkNgpcTools = async (): Promise<NgpcToolsReport> => {
  const lines: string[] = [];
  let ok = true;
  const fail = (line: string) => {
    ok = false;
    lines.push(`✕ ${line}`);
  };

  const v3Root = await getV3Root();
  if (await pathExists(Path.join(v3Root, "ngpcraft"))) {
    lines.push(`✓ NgpCraft V3: ${v3Root}`);
  } else {
    fail(`NgpCraft V3 not found: ${v3Root}`);
  }

  const python = getPython();
  const version = spawnSync(python, ["--version"], { encoding: "utf8" });
  const versionText = `${version.stdout ?? ""}${version.stderr ?? ""}`.trim();
  const match = versionText.match(/Python (\d+)\.(\d+)/);
  if (version.error || !match) {
    fail(`Python not found: "${python}"`);
  } else if (
    Number(match[1]) < 3 ||
    (Number(match[1]) === 3 && Number(match[2]) < 10)
  ) {
    fail(`${versionText} is too old (3.10 or newer needed): "${python}"`);
  } else {
    lines.push(`✓ ${versionText}: ${python}`);
  }

  if (!isWindows) {
    const wine = getWine();
    const wineVersion = spawnSync(wine, ["--version"], { encoding: "utf8" });
    if (wineVersion.error) {
      fail(`Wine not found: "${wine}"`);
    } else {
      lines.push(`✓ ${(wineVersion.stdout || "wine").trim()}: ${wine}`);
    }
  }

  const toolchain = getToolchainPath();
  if (!toolchain) {
    fail("Toshiba toolchain folder not set");
  } else if (ok) {
    const check = spawnSync(
      python,
      [
        "-B",
        "-m",
        "ngpcraft",
        "check-toolchain",
        "--toolchain",
        toolchain,
      ],
      { cwd: v3Root, encoding: "utf8", env: v3Env() },
    );
    try {
      const report = JSON.parse(check.stdout);
      if (report.ready) {
        lines.push(`✓ Toshiba toolchain: ${toolchain}`);
      } else {
        fail(
          `Toshiba toolchain incomplete (${toolchain}), missing: ${(report.missing ?? []).join(", ")}`,
        );
      }
    } catch (e) {
      fail(`Toshiba toolchain check failed: ${check.stderr || check.stdout}`);
    }
  } else {
    lines.push(
      `… Toshiba toolchain: ${toolchain} (checked once the above is fixed)`,
    );
  }

  return { ok, lines };
};
