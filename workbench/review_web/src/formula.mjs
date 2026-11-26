import katex from "../../../sourcelens/web/node_modules/katex/dist/katex.mjs";

const SYMBOLS = {
  "~": "\\neg",
  "!": "\\neg",
  "&": "\\land",
  "|": "\\lor",
  "->": "\\to",
  "<->": "\\leftrightarrow",
};
const UNICODE = {
  "¬": "~",
  "∧": "&",
  "∨": "|",
  "→": "->",
  "↔": "<->",
  "⊤": "TRUE",
  "⊥": "FALSE",
};

/** Typeset solver notation without rewriting names, grouping or quantifier scope.
 * This is a display adapter; the Python parsers remain the syntax authority.
 */
export function formulaToTex(source, semantics) {
  if (typeof source !== "string" || !source.trim()) return "";
  const firstOrder = semantics === "classical-first-order-v1";
  if (source.length > (firstOrder ? 4000 : 1000))
    throw new Error("Formula exceeds display budget");
  const text = firstOrder
    ? source.trim()
    : source.replace(/[¬∧∨→↔⊤⊥]/g, (symbol) => UNICODE[symbol]).trim();
  const token = firstOrder
    ? /\s*(->|[A-Za-z][A-Za-z0-9_]*|[~&|()=.,])/y
    : /\s*(<->|->|[A-Za-z][A-Za-z0-9_]*|[~!&|()])/y;
  const parts = [];
  let depth = 0;
  while (token.lastIndex < text.length) {
    const match = token.exec(text);
    if (!match) throw new Error("Unsupported formula notation");
    const value = match[1];
    if (value === "(") depth++;
    if (value === ")") depth--;
    if (depth < 0 || depth > 40 || parts.length >= (firstOrder ? 600 : 160))
      throw new Error("Formula exceeds display budget");
    if (SYMBOLS[value]) parts.push(SYMBOLS[value]);
    else if (firstOrder && ["forall", "exists"].includes(value))
      parts.push("\\" + value);
    else if (!firstOrder && ["TRUE", "FALSE"].includes(value))
      parts.push(value === "TRUE" ? "\\top" : "\\bot");
    else if (/^[A-Za-z][A-Za-z0-9_]*$/.test(value))
      // A multi-letter atom is one identifier, not a product or a subscript.
      parts.push(
        value.length === 1
          ? value
          : "\\mathit{" + value.replaceAll("_", "\\_") + "}",
      );
    else parts.push(value);
  }
  if (depth !== 0) throw new Error("Unbalanced formula parentheses");
  return parts.join(" ");
}

export function typesetFormula(source, semantics) {
  try {
    const tex = formulaToTex(source, semantics);
    return tex
      ? katex.renderToString(tex, {
          displayMode: false,
          output: "htmlAndMathml",
          trust: false,
          strict: "error",
          throwOnError: true,
          maxExpand: 100,
          maxSize: 10,
        })
      : null;
  } catch {
    // In-progress or unsupported text remains visible and editable as text.
    return null;
  }
}
