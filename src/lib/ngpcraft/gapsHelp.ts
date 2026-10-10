/*
 * NgpCraft: Help > "NGPC gaps": the V3 document of the differences between
 * GB Studio and the NGPC build (docs/ECARTS_GBSTUDIO.md, bundled with the
 * V3), shown as a page in the system browser. GB Studio's documentation stays
 * the reference for the events; this page says where the NGPC differs.
 */
import Path from "path";
import { app, shell } from "electron";
import { outputFile, pathExists, readFile } from "fs-extra";
import { getV3Root } from "lib/ngpcraft/v3Paths";

const escape = (text: string) =>
  text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const inline = (text: string) =>
  escape(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");

// The subset of Markdown the V3 documents use: headings, paragraphs, lists,
// tables, fenced code, inline code, bold and italics.
export const markdownToHtml = (markdown: string): string => {
  const out: string[] = [];
  const lines = markdown.split(/\r?\n/);
  let paragraph: string[] = [];
  let list = false;
  let code = false;
  const flush = () => {
    if (paragraph.length) {
      out.push(`<p>${inline(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
    if (list) {
      out.push("</ul>");
      list = false;
    }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("```")) {
      flush();
      out.push(code ? "</code></pre>" : "<pre><code>");
      code = !code;
      continue;
    }
    if (code) {
      out.push(escape(line));
      continue;
    }
    const heading = line.match(/^(#{1,4}) (.*)$/);
    if (heading) {
      flush();
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }
    if (line.startsWith("|")) {
      flush();
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        const cells = lines[i].split("|").slice(1, -1);
        if (!cells.every((c) => /^\s*:?-+:?\s*$/.test(c))) {
          rows.push(cells.map((c) => c.trim()));
        }
        i++;
      }
      i--;
      out.push(
        "<table>" +
          rows
            .map(
              (row, r) =>
                `<tr>${row
                  .map((c) =>
                    r === 0 ? `<th>${inline(c)}</th>` : `<td>${inline(c)}</td>`,
                  )
                  .join("")}</tr>`,
            )
            .join("") +
          "</table>",
      );
      continue;
    }
    const item = line.match(/^\s*[-*] (.*)$/);
    if (item) {
      if (paragraph.length) {
        out.push(`<p>${inline(paragraph.join(" "))}</p>`);
        paragraph = [];
      }
      if (!list) {
        out.push("<ul>");
        list = true;
      }
      out.push(`<li>${inline(item[1])}</li>`);
      continue;
    }
    if (!line.trim()) {
      flush();
      continue;
    }
    if (list && /^\s+/.test(line)) {
      // Continuation of the previous list item.
      out[out.length - 1] = out[out.length - 1].replace(
        /<\/li>$/,
        ` ${inline(line.trim())}</li>`,
      );
      continue;
    }
    paragraph.push(line.trim());
  }
  flush();
  return out.join("\n");
};

const PAGE_STYLE = `body{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:2em auto;padding:0 16px;color:#222;background:#fff}
code{background:#f0f0f0;padding:0 3px;border-radius:3px}pre{background:#f0f0f0;padding:8px;overflow:auto}
table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:3px 6px;vertical-align:top}
@media (prefers-color-scheme: dark){body{color:#ddd;background:#1e1e1e}code,pre{background:#2a2a2a}td,th{border-color:#444}}`;

export const openNgpcGaps = async (): Promise<string | undefined> => {
  const source = Path.join(await getV3Root(), "docs", "ECARTS_GBSTUDIO.md");
  if (!(await pathExists(source))) {
    return `NGPC gaps document not found: ${source}`;
  }
  const html =
    `<!doctype html><html><head><meta charset="utf-8"><title>NgpCraft: NGPC gaps</title>` +
    `<style>${PAGE_STYLE}</style></head><body>` +
    markdownToHtml(await readFile(source, "utf8")) +
    "</body></html>";
  const page = Path.join(app.getPath("temp"), "ngpcraft-ngpc-gaps.html");
  await outputFile(page, html);
  const error = await shell.openPath(page);
  return error || undefined;
};
