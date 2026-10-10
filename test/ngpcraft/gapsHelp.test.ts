/*
 * NgpCraft: Help > NGPC gaps renders the V3 document (ECARTS_GBSTUDIO.md).
 */
import Path from "path";
import { readFileSync, existsSync } from "fs";
import { markdownToHtml } from "lib/ngpcraft/gapsHelp";
import { ngpcraftDefaultV3Root } from "consts";

test("headings, lists, tables and code become HTML", () => {
  const html = markdownToHtml(
    "# T\n\nA `x` **b**\n\n- one\n  two\n- three\n\n| a | b |\n|---|---|\n| 1 | <2> |\n",
  );
  expect(html).toContain("<h1>T</h1>");
  expect(html).toContain("<p>A <code>x</code> <strong>b</strong></p>");
  expect(html).toContain("<li>one two</li>");
  expect(html).toContain("<th>a</th>");
  expect(html).toContain("<td>&lt;2&gt;</td>");
});

const gaps = Path.join(ngpcraftDefaultV3Root, "docs/ECARTS_GBSTUDIO.md");

(existsSync(gaps) ? test : test.skip)("the V3 gaps document renders", () => {
  const markdown = readFileSync(gaps, "utf8");
  const html = markdownToHtml(markdown);
  const sections = (markdown.match(/^## /gm) ?? []).length;
  expect((html.match(/<h2>/g) ?? []).length).toBe(sections);
  expect(html).not.toContain("```");
});
