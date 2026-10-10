/*
 * NgpCraft: load the V3 event catalogue (main process, read-only).
 * Source files in the V3 folder: ngpcraft/parity/gbstudio.json for the
 * statuses, docs/ECARTS_GBSTUDIO.md for the titles of the gaps (§N).
 */
import Path from "path";
import { pathExists, readFile, readJSON } from "fs-extra";
import {
  NgpcCatalogue,
  NgpcCatalogueEvent,
  NgpcEventStatus,
} from "shared/lib/ngpcraft/catalogue";
import { getV3Root } from "lib/ngpcraft/v3Paths";

interface ParityEntry {
  status?: string;
  ecart?: string;
  partiel?: string;
  raison?: string;
  note?: string;
}

const STATUSES: NgpcEventStatus[] = ["identique", "adapte", "refuse"];

// "## 12. Fonctions propres au Game Boy — ✅" -> { "§12": "Fonctions ..." }
const readGapTitles = async (
  v3Root: string,
): Promise<Record<string, string>> => {
  const path = Path.join(v3Root, "docs", "ECARTS_GBSTUDIO.md");
  if (!(await pathExists(path))) {
    return {};
  }
  const titles: Record<string, string> = {};
  const text = await readFile(path, "utf8");
  for (const match of text.matchAll(/^## (\d+)\. (.+)$/gm)) {
    titles[`§${match[1]}`] = match[2].split(" — ")[0].trim();
  }
  return titles;
};

// Returns undefined when the V3 is not found: the editor then filters nothing.
export const loadNgpcCatalogue = async (): Promise<
  NgpcCatalogue | undefined
> => {
  const v3Root = await getV3Root();
  const path = Path.join(v3Root, "ngpcraft", "parity", "gbstudio.json");
  if (!(await pathExists(path))) {
    return undefined;
  }
  const parity = await readJSON(path);
  const titles = await readGapTitles(v3Root);
  const events: Record<string, NgpcCatalogueEvent> = {};
  for (const [id, entry] of Object.entries(
    (parity.events ?? {}) as Record<string, ParityEntry>,
  )) {
    const status = STATUSES.find((s) => s === entry.status);
    if (!status) {
      continue;
    }
    events[id] = {
      status,
      ecart: entry.ecart,
      ecartTitle: entry.ecart ? titles[entry.ecart] : undefined,
      detail: entry.raison ?? entry.partiel ?? entry.note,
    };
  }
  return { events };
};
