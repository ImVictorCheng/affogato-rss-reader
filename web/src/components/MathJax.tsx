import { type ReactNode, useEffect, useRef } from "react";

type MathJaxApi = {
  startup?: { promise?: Promise<void> };
  typesetClear?: (elements: HTMLElement[]) => void;
  typesetPromise?: (elements: HTMLElement[]) => Promise<void>;
};

declare global {
  interface Window {
    MathJax?: MathJaxApi & Record<string, unknown>;
  }
}

type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

const scriptSelector = "script[data-affogato-mathjax]";
let loadPromise: Promise<MathJaxApi> | null = null;
let typesetQueue: Promise<void> = Promise.resolve();
const MAX_MATH_SOURCE_CHARS = 100_000;
const MAX_MATH_DELIMITER_MARKERS = 512;

function mathJaxConfig(): MathJaxApi & Record<string, unknown> {
  return {
    loader: {
      load: ["ui/safe"],
    },
    tex: {
      inlineMath: [["$", "$"], ["\\(", "\\)"]],
      displayMath: [["$$", "$$"], ["\\[", "\\]"]],
      processEscapes: true,
      // This is an exact allowlist.  The full distribution is copied locally
      // so AMS and error fallbacks are available without network requests, but
      // untrusted feed/LLM text must never enable autoload, require, html, or
      // other DOM-producing TeX extensions.
      packages: ["base", "ams", "noerrors", "noundefined"],
      maxBuffer: 5 * 1024,
      maxMacros: 1_000,
    },
    safe: {
      allow: {
        URLs: "none",
        classes: "none",
        cssIDs: "none",
        styles: "none",
      },
    },
    options: {
      skipHtmlTags: ["script", "noscript", "style", "textarea", "pre", "code"],
    },
    startup: { typeset: false } as unknown as { promise?: Promise<void> },
  };
}

async function readyMathJax(): Promise<MathJaxApi> {
  const mathJax = window.MathJax;
  await mathJax?.startup?.promise;
  if (!mathJax?.typesetPromise) throw new Error("MathJax loaded without typesetPromise");
  return mathJax;
}

function loadMathJax(): Promise<MathJaxApi> {
  if (window.MathJax?.typesetPromise) return readyMathJax();
  if (loadPromise) return loadPromise;

  loadPromise = new Promise<MathJaxApi>((resolve, reject) => {
    const fail = (error: unknown, script: HTMLScriptElement) => {
      script.remove();
      delete window.MathJax;
      loadPromise = null;
      reject(error instanceof Error ? error : new Error(String(error)));
    };
    const finish = (script: HTMLScriptElement) => {
      void readyMathJax().then(resolve, (error) => fail(error, script));
    };
    const existing = document.querySelector<HTMLScriptElement>(scriptSelector);
    if (existing) {
      existing.addEventListener("load", () => finish(existing), { once: true });
      existing.addEventListener("error", () => fail(new Error("Unable to load MathJax"), existing), { once: true });
      return;
    }

    window.MathJax = mathJaxConfig();
    const script = document.createElement("script");
    script.dataset.affogatoMathjax = "";
    script.async = true;
    script.src = `${import.meta.env.BASE_URL}vendor/mathjax/tex-chtml.js`;
    script.addEventListener("load", () => finish(script), { once: true });
    script.addEventListener("error", () => fail(new Error("Unable to load MathJax"), script), { once: true });
    document.head.append(script);
  });
  return loadPromise;
}

function queueTypeset(element: HTMLElement): Promise<void> {
  typesetQueue = typesetQueue.catch(() => undefined).then(async () => {
    const mathJax = await loadMathJax();
    if (!element.isConnected) return;
    mathJax.typesetClear?.([element]);
    await mathJax.typesetPromise?.([element]);
  });
  return typesetQueue;
}

export function containsMath(source: string): boolean {
  return /(^|[^\\])\$\$[\s\S]+?\$\$/.test(source)
    || /(^|[^\\])\$(?!\$)(?=\S)(?:\\.|[^$\n])+?\$/.test(source)
    || /\\\([\s\S]+?\\\)/.test(source)
    || /\\\[[\s\S]+?\\\]/.test(source);
}

function typesetWorkIsBounded(source: string): boolean {
  if (source.length > MAX_MATH_SOURCE_CHARS) return false;
  let markers = 0;
  for (let index = 0; index < source.length; index += 1) {
    if (
      source[index] === "$"
      || (source[index] === "\\" && "([".includes(source[index + 1] ?? ""))
    ) {
      markers += 1;
      if (markers > MAX_MATH_DELIMITER_MARKERS) return false;
    }
  }
  return true;
}

function fenceAt(line: string): { marker: "`" | "~"; length: number } | null {
  const match = /^ {0,3}(`{3,}|~{3,})/.exec(line);
  if (!match) return null;
  return { marker: match[1][0] as "`" | "~", length: match[1].length };
}

function scanMarkdownMath(source: string): { markdown: string; searchable: string } {
  const output: string[] = [];
  const searchable: string[] = [];
  let fence: { marker: "`" | "~"; length: number } | null = null;
  let inlineTicks = 0;

  for (const line of source.split(/(?<=\n)/)) {
    const candidate = fenceAt(line);
    if (fence) {
      output.push(line);
      searchable.push("\n");
      if (candidate?.marker === fence.marker && candidate.length >= fence.length) fence = null;
      continue;
    }
    if (!inlineTicks && candidate) {
      fence = candidate;
      output.push(line);
      searchable.push("\n");
      continue;
    }

    let index = 0;
    while (index < line.length) {
      const character = line[index];
      if (character === "`") {
        let end = index + 1;
        while (line[end] === "`") end += 1;
        const length = end - index;
        if (!inlineTicks) inlineTicks = length;
        else if (inlineTicks === length) inlineTicks = 0;
        output.push(line.slice(index, end));
        searchable.push(" ".repeat(length));
        index = end;
        continue;
      }

      if (!inlineTicks && character === "\\" && "()[]".includes(line[index + 1] ?? "")) {
        let precedingSlashes = 0;
        for (let cursor = index - 1; cursor >= 0 && line[cursor] === "\\"; cursor -= 1) precedingSlashes += 1;
        if (precedingSlashes % 2 === 0) {
          const replacement = line[index + 1] === "(" || line[index + 1] === ")" ? "$" : "$$";
          output.push(replacement);
          searchable.push(replacement);
          index += 2;
          continue;
        }
      }

      output.push(character);
      searchable.push(inlineTicks ? " " : character);
      index += 1;
    }
  }
  return { markdown: output.join(""), searchable: searchable.join("") };
}

export function prepareMathMarkdown(source: string): string {
  return scanMarkdownMath(source).markdown;
}

export function containsMarkdownMath(source: string): boolean {
  return containsMath(scanMarkdownMath(source).searchable);
}

function nodeText(node: HastNode): string {
  if (node.type === "text") return node.value ?? "";
  return node.children?.map(nodeText).join("") ?? "";
}

function classNames(node: HastNode): string[] {
  const value = node.properties?.className;
  if (Array.isArray(value)) return value.map(String);
  return typeof value === "string" ? value.split(/\s+/) : [];
}

function mathSource(value: string, display: boolean): HastNode {
  return {
    type: "element",
    tagName: display ? "div" : "span",
    properties: { className: ["mathjax-source", display ? "mathjax-source--display" : "mathjax-source--inline"] },
    children: [{ type: "text", value: display ? `\\[${value.trim()}\\]` : `\\(${value.trim()}\\)` }],
  };
}

function transformMathNodes(node: HastNode): void {
  if (!node.children) return;
  node.children = node.children.map((child) => {
    if (child.tagName === "code" && classNames(child).includes("math-inline")) {
      return mathSource(nodeText(child), false);
    }
    const displayCode = child.tagName === "pre" && child.children?.length === 1
      ? child.children[0]
      : undefined;
    if (displayCode?.tagName === "code" && classNames(displayCode).includes("math-display")) {
      return mathSource(nodeText(displayCode), true);
    }
    transformMathNodes(child);
    return child;
  });
}

export function rehypeMathJaxSource() {
  return (tree: HastNode) => transformMathNodes(tree);
}

export function MathJaxScope({ source, enabled = containsMath(source), children }: {
  source: string;
  enabled?: boolean;
  children: ReactNode;
}) {
  const root = useRef<HTMLDivElement>(null);
  const shouldTypeset = enabled && typesetWorkIsBounded(source);

  useEffect(() => {
    const element = root.current;
    if (!element || !shouldTypeset) return;
    let current = true;
    element.dataset.mathjaxStatus = "pending";
    void queueTypeset(element).then(
      () => { if (current) element.dataset.mathjaxStatus = "ready"; },
      (error: unknown) => {
        if (!current) return;
        element.dataset.mathjaxStatus = "error";
        element.dataset.mathjaxError = error instanceof Error ? error.message : String(error);
      },
    );
    return () => {
      current = false;
      window.MathJax?.typesetClear?.([element]);
    };
  }, [shouldTypeset, source]);

  return <div className="mathjax-scope" key={source} ref={root}>{children}</div>;
}

export function resetMathJaxForTests() {
  loadPromise = null;
  typesetQueue = Promise.resolve();
}
