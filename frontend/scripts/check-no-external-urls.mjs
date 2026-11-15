// CHR-ENV-002: fail if the built frontend would load anything from outside the LAN.
// Only URLs in a loading context (url(), @import, src=/href=, fetch(), import(), ...)
// count, so plain strings like SVG xmlns or React's error-doc links do not.
// ponytail: a URL hoisted into a constant before use (const A="https://x";fetch(A))
// or a later srcset candidate is not seen; a real offline browser test catches those.
// Usage: node scripts/check-no-external-urls.mjs [distDir]
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { extname, join } from "node:path";

const LOAD_REF =
  /(?:url\(\s*|image-set\(\s*|@import\s*(?:url\(\s*)?|\b(?:src|href|action|poster|srcset)["']?\s*[=:]\s*|\b(?:fetch|import|importScripts|sendBeacon|EventSource|WebSocket|Worker|URL)\s*\(\s*|\b(?:import|from)\s*(?=["'`]))["'`]?((?:(?:https?|wss?):)?\/\/([^/?#\\"'`\s)]+)[^"'`\s)]*)/gi;

const LAN_HOST =
  /^(localhost|[\w-]+\.(local|lan)|0\.0\.0\.0|\[::1\]|\[f[cde][0-9a-f]*:[0-9a-f:]*\]|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|169\.254\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)\.?$/i;

export function findExternalRefs(text) {
  // Minified JS escapes quotes and slashes inside strings (url(\"https:\/\/...\")).
  const unescaped = text.replace(/\\(?=[/"'])/g, "");
  const hits = [];
  for (const [, url, authority] of unescaped.matchAll(LOAD_REF)) {
    const host = authority.replace(/^[^@]*@/, "").replace(/:\d+$/, "");
    if (!LAN_HOST.test(host)) hits.push(url);
  }
  return hits;
}

const SCANNED = [".html", ".css", ".js", ".mjs", ".cjs", ".svg", ".json", ".webmanifest", ".xml"];

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(path);
    else if (SCANNED.includes(extname(path))) yield path;
  }
}

if (import.meta.main) {
  const dist = process.argv[2] ?? "dist";
  if (!existsSync(dist)) {
    console.error(`${dist} does not exist; run the build first.`);
    process.exit(1);
  }
  let scanned = 0;
  let failed = false;
  for (const file of walk(dist)) {
    scanned++;
    for (const url of findExternalRefs(readFileSync(file, "utf8"))) {
      console.error(`${file}: external reference ${url}`);
      failed = true;
    }
  }
  if (scanned === 0) {
    console.error(`No HTML/CSS/JS files found in ${dist}; run the build first.`);
    process.exit(1);
  }
  if (failed) process.exit(1);
  console.log(`OK: ${scanned} built files reference no external origin.`);
}
