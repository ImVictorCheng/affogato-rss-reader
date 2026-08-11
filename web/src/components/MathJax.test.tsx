import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  containsMarkdownMath,
  MathJaxScope,
  prepareMathMarkdown,
  rehypeMathJaxSource,
  resetMathJaxForTests,
} from "./MathJax";

function mockMathJax(options: { reject?: boolean } = {}) {
  const typesetClear = vi.fn();
  const typesetPromise = options.reject
    ? vi.fn().mockRejectedValue(new Error("bad formula"))
    : vi.fn().mockResolvedValue(undefined);
  window.MathJax = {
    startup: { promise: Promise.resolve() },
    typesetClear,
    typesetPromise,
  };
  return { typesetClear, typesetPromise };
}

afterEach(() => {
  delete window.MathJax;
  document.querySelectorAll("script[data-affogato-mathjax]").forEach((script) => script.remove());
  resetMathJaxForTests();
});

describe("MathJax rendering", () => {
  it("loads only the approved TeX packages and disables DOM-affecting input", async () => {
    render(<MathJaxScope source="$x$"><p>$x$</p></MathJaxScope>);

    await waitFor(() => expect(document.querySelector("script[data-affogato-mathjax]")).toBeInTheDocument());
    const config = window.MathJax as {
      loader?: { load?: string[] };
      tex?: { packages?: string[]; maxBuffer?: number; maxMacros?: number };
      safe?: { allow?: Record<string, string> };
    };
    expect(config.loader?.load).toEqual(["ui/safe"]);
    expect(config.tex?.packages).toEqual(["base", "ams", "noerrors", "noundefined"]);
    expect(config.tex?.packages).not.toEqual(expect.arrayContaining(["autoload", "require", "html"]));
    expect(config.tex?.maxBuffer).toBe(5 * 1024);
    expect(config.tex?.maxMacros).toBe(1_000);
    expect(config.safe?.allow).toEqual({
      URLs: "none",
      classes: "none",
      cssIDs: "none",
      styles: "none",
    });

    document.querySelector<HTMLScriptElement>("script[data-affogato-mathjax]")
      ?.dispatchEvent(new Event("error"));
    await waitFor(() => expect(document.querySelector(".mathjax-scope")).toHaveAttribute("data-mathjax-status", "error"));
  });

  it("removes a failed loader and retries safely on the next typeset", async () => {
    const view = render(<MathJaxScope source="$first$"><p>$first$</p></MathJaxScope>);
    const first = await waitFor(() => {
      const script = document.querySelector<HTMLScriptElement>("script[data-affogato-mathjax]");
      expect(script).toBeInTheDocument();
      return script!;
    });
    first.dispatchEvent(new Event("error"));
    await waitFor(() => expect(document.querySelector(".mathjax-scope")).toHaveAttribute("data-mathjax-status", "error"));
    expect(first).not.toBeInTheDocument();

    const mathJax = mockMathJax();
    view.rerender(<MathJaxScope source="$second$"><p>$second$</p></MathJaxScope>);
    await waitFor(() => expect(mathJax.typesetPromise).toHaveBeenCalledOnce());
    expect(screen.getByText("$second$").closest(".mathjax-scope")).toHaveAttribute("data-mathjax-status", "ready");
  });

  it("recognizes and normalizes all supported delimiters outside Markdown code", () => {
    const markdown = [
      "Inline \\(a*b_c\\) and $x_i$.",
      "",
      "\\[",
      "\\begin{aligned}a&=b\\\\c&=d\\end{aligned}",
      "\\]",
      "",
      "`\\(inline_code\\)`",
      "",
      "```tex",
      "\\[fenced_code\\]",
      "```",
    ].join("\n");

    const prepared = prepareMathMarkdown(markdown);
    expect(prepared).toContain("$a*b_c$");
    expect(prepared).toContain("$$\n\\begin{aligned}");
    expect(prepared).toContain("`\\(inline_code\\)`");
    expect(prepared).toContain("\\[fenced_code\\]");
    expect(containsMarkdownMath(markdown)).toBe(true);
    expect(containsMarkdownMath("`$code$`\n\n```tex\n$$code$$\n```")).toBe(false);
  });

  it("turns remark math nodes into MathJax-readable elements", () => {
    const tree = {
      type: "root",
      children: [
        { type: "element", tagName: "p", children: [
          { type: "element", tagName: "code", properties: { className: ["language-math", "math-inline"] }, children: [{ type: "text", value: "a*b_c" }] },
        ] },
        { type: "element", tagName: "pre", children: [
          { type: "element", tagName: "code", properties: { className: ["language-math", "math-display"] }, children: [{ type: "text", value: "x^2" }] },
        ] },
      ],
    };

    rehypeMathJaxSource()(tree);
    expect(tree.children[0].children[0]).toMatchObject({ tagName: "span", children: [{ value: "\\(a*b_c\\)" }] });
    expect(tree.children[1]).toMatchObject({ tagName: "div", children: [{ value: "\\[x^2\\]" }] });
  });

  it("typesets a scope again when its source changes and skips plain text", async () => {
    const mathJax = mockMathJax();
    const view = render(<MathJaxScope source="$x$"><p>$x$</p></MathJaxScope>);

    await waitFor(() => expect(mathJax.typesetPromise).toHaveBeenCalledOnce());
    expect(screen.getByText("$x$").closest(".mathjax-scope")).toHaveAttribute("data-mathjax-status", "ready");

    view.rerender(<MathJaxScope source="$y$"><p>$y$</p></MathJaxScope>);
    await waitFor(() => expect(mathJax.typesetPromise).toHaveBeenCalledTimes(2));
    expect(mathJax.typesetClear).toHaveBeenCalled();

    view.rerender(<MathJaxScope source="plain text"><p>plain text</p></MathJaxScope>);
    await Promise.resolve();
    expect(mathJax.typesetPromise).toHaveBeenCalledTimes(2);
  });

  it("keeps raw TeX readable when MathJax rejects a formula", async () => {
    mockMathJax({ reject: true });
    render(<MathJaxScope source="$broken{$"><p>$broken&#123;$</p></MathJaxScope>);

    const source = screen.getByText("$broken{$");
    await waitFor(() => expect(source.closest(".mathjax-scope")).toHaveAttribute("data-mathjax-status", "error"));
    expect(source).toBeVisible();
  });

  it.each([
    ["an oversized source", `$$${"x".repeat(100_001)}$$`],
    ["too many equations", "$x$".repeat(300)],
  ])("leaves raw TeX for %s instead of starting unbounded work", async (_label, source) => {
    const mathJax = mockMathJax();
    render(<MathJaxScope source={source}><p>raw formula fallback</p></MathJaxScope>);

    await Promise.resolve();
    expect(mathJax.typesetPromise).not.toHaveBeenCalled();
    expect(screen.getByText("raw formula fallback")).toBeVisible();
  });
});
