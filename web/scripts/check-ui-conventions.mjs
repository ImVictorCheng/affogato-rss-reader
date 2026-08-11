import { readdir, readFile } from "node:fs/promises";
import { extname, join, relative, resolve } from "node:path";
import * as ts from "typescript";

const sourceRoot = resolve("src");
const forbidden = [
  { name: "native <select>", pattern: /<\s*select\b/giu },
  { name: "native <datalist>", pattern: /<\s*datalist\b/giu },
  { name: "native input[list]", pattern: /<\s*input\b[^>]*\blist\s*=/giu },
];
const nonTextInputTypes = new Set(["checkbox", "radio", "color", "file", "range", "button", "submit", "reset", "image", "hidden"]);
const fieldContexts = new Set(["field", "field-with-action", "field-control", "search-box", "tag-input-wrap", "custom-domain-form", "app-combobox__input", "brief-rule-editor"]);

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

  const source = ts.createSourceFile(file, content, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const attributesOf = (node) => ts.isJsxElement(node) ? node.openingElement.attributes : node.attributes;
  const attribute = (node, name) => {
    for (const property of attributesOf(node).properties) {
      if (!ts.isJsxAttribute(property) || property.name.text !== name) continue;
      if (!property.initializer) return "";
      return ts.isStringLiteral(property.initializer) ? property.initializer.text : null;
    }
    return undefined;
  };
  const tagName = (node) => (ts.isJsxElement(node) ? node.openingElement.tagName : node.tagName).getText();
  const visit = (node, ancestors = []) => {
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = tagName(node);
      if (tag === "input" || tag === "textarea") {
        const type = tag === "input" ? attribute(node, "type") : undefined;
        const exempt = tag === "input" && type && nonTextInputTypes.has(type);
        const ownClasses = (attribute(node, "className") || "").split(/\s+/u);
        const ancestorClasses = ancestors.flatMap((ancestor) => (attribute(ancestor, "className") || "").split(/\s+/u));
        const hasFieldContract = ownClasses.some((token) => fieldContexts.has(token)) || ancestorClasses.some((token) => fieldContexts.has(token));
        if (!exempt && !hasFieldContract) {
          const line = source.getLineAndCharacterOfPosition(node.getStart()).line + 1;
          violations.push(`${relative(resolve("."), file)}:${line}: text input must use .field, .field-with-action, .field-control, or an approved compact-input context`);
        }
      }
      const nextAncestors = [...ancestors, node];
      ts.forEachChild(node, (child) => visit(child, nextAncestors));
      return;
    }
    ts.forEachChild(node, (child) => visit(child, ancestors));
  };
  visit(source);
}

if (violations.length) {
  console.error("UI convention check failed:");
  violations.forEach((violation) => console.error(`  ${violation}`));
  console.error("Use SelectMenu/ComboBox for dropdowns and the shared field contract for text inputs.");
  process.exitCode = 1;
} else {
  console.log("UI convention check passed.");
}
