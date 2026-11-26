import React, { useMemo } from "react";
import { typesetFormula } from "./formula.mjs";
import "../../../sourcelens/web/node_modules/katex/dist/katex.min.css";

export default function Formula({ value, semantics }) {
  const html = useMemo(
    () => typesetFormula(value, semantics),
    [value, semantics],
  );
  if (!value) return <span className="formula-empty">No formula</span>;
  return html ? (
    <div
      className="formula"
      data-formula={value}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  ) : (
    <code className="formula formula-fallback">{value}</code>
  );
}
