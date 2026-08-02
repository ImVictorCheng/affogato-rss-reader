import { readdir, readFile } from "node:fs/promises";
import { extname, join, relative, resolve } from "node:path";

const sourceRoot = resolve("src");
const forbidden = [
  { name: "native <select>", pattern: /<\s*select\b/giu },
  { name: "native <datalist>", pattern: /<\s*datalist\b/giu },
  { name: "native input[list]", pattern: /<\s*input\b[^>]*\blist\s*=/giu },
];

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map(async (entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return [".tsx", ".jsx"].includes(extname(entry.name)) ? [path] : [];
  }));
  return nested.flat();
}

const violations = [];
for (const file of await sourceFiles(sourceRoot)) {
  const content = await readFile(file, "utf8");
  for (const rule of forbidden) {
    for (const match of content.matchAll(rule.pattern)) {
      const line = content.slice(0, match.index).split("\n").length;
      violations.push(`${relative(resolve("."), file)}:${line}: ${rule.name}`);
    }
  }
}

if (violations.length) {
  console.error("Dropdown convention check failed:");
  violations.forEach((violation) => console.error(`  ${violation}`));
  console.error("Use SelectMenu or ComboBox from src/components/Common.tsx.");
  process.exitCode = 1;
} else {
  console.log("Dropdown convention check passed.");
}
