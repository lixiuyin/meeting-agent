import { readFileSync, readdirSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { basename, join } from "node:path";

const distDir = new URL("../dist/", import.meta.url);
const assetsDir = new URL("assets/", distDir);
const html = readFileSync(new URL("index.html", distDir), "utf8");
const entryMatch = html.match(/<script[^>]+src="\/assets\/([^"]+\.js)"/);
if (!entryMatch)
  throw new Error("Unable to identify the JavaScript entry chunk in dist/index.html");

const limits = {
  entryRaw: 800 * 1024,
  entryGzip: 260 * 1024,
  asyncRaw: 600 * 1024,
  asyncGzip: 180 * 1024,
};
const failures = [];
// A shared dependency accidentally assigned to the PDF manual chunk can pull
// the entire lazy reader into the entry graph, while every chunk stays in budget.
if (/<link[^>]+rel="modulepreload"[^>]+href="\/assets\/pdf-[^"]+\.js"/.test(html)) {
  failures.push("The lazy PDF reader must not be preloaded by the initial page");
}
for (const name of readdirSync(assetsDir).filter((value) => value.endsWith(".js"))) {
  const bytes = readFileSync(join(assetsDir.pathname, name));
  const gzipBytes = gzipSync(bytes).byteLength;
  const entry = basename(name) === entryMatch[1];
  const rawLimit = entry ? limits.entryRaw : limits.asyncRaw;
  const gzipLimit = entry ? limits.entryGzip : limits.asyncGzip;
  if (bytes.byteLength > rawLimit || gzipBytes > gzipLimit) {
    failures.push(`${name}: raw=${bytes.byteLength}/${rawLimit}, gzip=${gzipBytes}/${gzipLimit}`);
  }
}

if (failures.length) {
  throw new Error(`Bundle size budget exceeded:\n${failures.join("\n")}`);
}
console.log("Bundle size budget passed.");
