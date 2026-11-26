import assert from "node:assert/strict";
import test from "node:test";
import { formulaToTex, typesetFormula } from "../review_web/src/formula.mjs";

const FOL = "classical-first-order-v1";

test("propositional connectives, constants and Unicode retain their operators", () => {
  assert.equal(
    formulaToTex("!(p & q) -> (r | FALSE)"),
    "\\neg ( p \\land q ) \\to ( r \\lor \\bot )",
  );
  assert.equal(
    formulaToTex("¬(p ∧ q) ↔ (r ∨ ⊤)"),
    "\\neg ( p \\land q ) \\leftrightarrow ( r \\lor \\top )",
  );
});

test("explicit grouping and quantifier scope are preserved", () => {
  const source = "~(exists x. (W(x) & forall y. (W(y) -> y = x)))";
  assert.equal(
    formulaToTex(source, FOL),
    "\\neg ( \\exists x . ( W ( x ) \\land \\forall y . ( W ( y ) \\to y = x ) ) )",
  );
  assert.notEqual(
    formulaToTex("forall x. (P(x) & Q(x))", FOL),
    formulaToTex("(forall x. P(x)) & (forall x. Q(x))", FOL),
  );
});

test("one multi-letter identifier stays one literal name", () => {
  assert.equal(
    formulaToTex("User_knows & p2"),
    "\\mathit{User\\_knows} \\land \\mathit{p2}",
  );
  assert.equal(
    formulaToTex("forall -> exists"),
    "\\mathit{forall} \\to \\mathit{exists}",
  );
  assert.equal(
    formulaToTex("forall x. TRUE(x)", FOL),
    "\\forall x . \\mathit{TRUE} ( x )",
  );
});

test("binary predicates, equality and nested quantification are retained", () => {
  assert.equal(
    formulaToTex("forall x. exists y. (R(x,y) & ~(x = y))", FOL),
    "\\forall x . \\exists y . ( R ( x , y ) \\land \\neg ( x = y ) )",
  );
});

test("typesetting emits accessible math and the complete Quine conclusion", () => {
  const source =
    "(forall x. ~W(x)) | (exists x. (W(x) & exists y. (W(y) & ~(x = y))))";
  const html = typesetFormula(source, FOL);
  assert.match(html, /class="katex"/);
  assert.match(
    html,
    /<math[^>]*xmlns="http:\/\/www.w3.org\/1998\/Math\/MathML"/,
  );
  assert.ok(html.includes(formulaToTex(source, FOL)));
  assert.ok(!html.includes("…"));
  assert.equal(
    source,
    "(forall x. ~W(x)) | (exists x. (W(x) & exists y. (W(y) & ~(x = y))))",
  );
});

test("display accepts no TeX commands, HTML or external-resource instructions", () => {
  for (const source of [
    "\\href{https://example.com}{p}",
    "<img src=x onerror=alert(1)>",
    "\\includegraphics{https://example.com/x}",
    "p_{q}",
    "p $ q",
  ])
    assert.equal(typesetFormula(source), null, source);
});

test("incomplete grouping and display budgets fall back without throwing", () => {
  for (const source of [
    "~(",
    "p)",
    "(",
    "(".repeat(41) + "p" + ")".repeat(41),
    "p".repeat(1001),
    Array(161).fill("p").join(" "),
  ])
    assert.equal(typesetFormula(source), null);
});

test("first-order rendering does not widen the server's notation", () => {
  for (const source of ["∀x. P(x)", "P(x) <-> Q(x)", "!P(x)"])
    assert.equal(typesetFormula(source, FOL), null);
});

test("empty input has no fabricated formula", () => {
  assert.equal(typesetFormula(""), null);
  assert.equal(typesetFormula("  "), null);
});
