import { cp, mkdir, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const mathJaxRoot = path.join(webRoot, "node_modules", "mathjax", "es5");
const mathJaxPackageRoot = path.join(webRoot, "node_modules", "mathjax");
const targetRoot = path.join(webRoot, "public", "vendor", "mathjax");

await rm(targetRoot, { recursive: true, force: true });
await mkdir(path.join(targetRoot, "output", "chtml", "fonts"), { recursive: true });
await cp(
  path.join(mathJaxRoot, "tex-chtml-full.js"),
  path.join(targetRoot, "tex-chtml.js"),
);
await mkdir(path.join(targetRoot, "ui"), { recursive: true });
await cp(
  path.join(mathJaxRoot, "ui", "safe.js"),
  path.join(targetRoot, "ui", "safe.js"),
);
await cp(
  path.join(mathJaxRoot, "output", "chtml", "fonts", "woff-v2"),
  path.join(targetRoot, "output", "chtml", "fonts", "woff-v2"),
  { recursive: true },
);
await cp(
  path.join(mathJaxPackageRoot, "LICENSE"),
  path.join(targetRoot, "LICENSE"),
);

console.log("Prepared local MathJax CHTML assets.");
