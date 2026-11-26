"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  doc: null,
  examples: [],
  selected: 0,
  inspect: null,
  bundle: null,
  head: null,
  generation: 0,
  annotationError: null,
};
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "value") continue;
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "className") node.className = value;
    else if (key === "disabled") node.disabled = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat())
    node.append(
      child instanceof Node ? child : document.createTextNode(String(child)),
    );
  if ("value" in props) node.value = props.value;
  return node;
}
function message(text, error = false) {
  $("message").textContent = text;
  $("message").hidden = !text;
  $("message").classList.toggle("error", error);
}
async function api(path, payload) {
  const response = await fetch(
    path,
    payload === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
  );
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || "Local request failed");
  return value;
}
function action(fn) {
  return async () => {
    try {
      await fn();
    } catch (error) {
      message(error.message, true);
    }
  };
}
function selected() {
  return state.doc.readings[state.selected];
}
function field(label, value, change, tag = "input", props = {}) {
  return el(
    "label",
    {},
    el("span", {}, label),
    el(tag, {
      "aria-label": label,
      value: value ?? "",
      onchange: (event) => change(event.target.value),
      ...props,
    }),
  );
}
function select(label, value, choices, change) {
  return el(
    "label",
    {},
    el("span", {}, label),
    el(
      "select",
      {
        "aria-label": label,
        value,
        onchange: (event) => change(event.target.value),
      },
      choices.map((choice) => el("option", { value: choice }, choice)),
    ),
  );
}
function badge(value) {
  return el(
    "span",
    {
      className:
        "badge " +
        (value === "self_reviewed" ||
        value === "witness_found" ||
        value === "no_counterexample_complete"
          ? "green"
          : "amber"),
    },
    value.replaceAll("_", " "),
  );
}
function invalidate() {
  state.bundle = null;
  state.inspect = null;
  state.generation++;
  renderReview();
  renderAST();
  renderResults();
  refreshInspection();
}
function assertEditable() {
  if (state.annotationError)
    throw new Error(
      "Fix semantic annotations before using this draft: " +
        state.annotationError,
    );
}
function change(mutation) {
  mutation();
  invalidate();
}
async function refreshInspection() {
  if (state.annotationError) {
    message(state.annotationError, true);
    return;
  }
  const generation = state.generation;
  try {
    const result = await api("/api/inspect", { document: state.doc });
    if (generation !== state.generation) return;
    state.inspect = result;
    renderReview();
    renderAST();
    message(
      "Working content checked. Review judgments apply only to matching content.",
    );
  } catch (error) {
    if (generation !== state.generation) return;
    state.inspect = null;
    renderReview();
    $("asts").replaceChildren();
    message(error.message, true);
  }
}
function openDocument(doc, head = null) {
  state.doc = doc;
  state.head = head;
  state.selected = 0;
  state.inspect = null;
  state.bundle = null;
  state.annotationError = null;
  state.generation++;
  render();
  refreshInspection();
}
function render() {
  const doc = state.doc;
  $("source").replaceChildren(
    field("Reconstruction ID", doc.reconstruction_id, (value) =>
      change(() => {
        doc.reconstruction_id = value;
        doc.parent_revision = null;
        state.head = null;
        $("head").textContent = "New identity; no saved head.";
      }),
    ),
    field("Source key", doc.source.key, (value) =>
      change(() => {
        doc.source.key = value;
      }),
    ),
    field("Source version", doc.source.version, (value) =>
      change(() => {
        doc.source.version = value;
      }),
    ),
    select(
      "Material kind",
      doc.source.kind,
      ["authored_control", "authored_paraphrase", "original_excerpt"],
      (value) =>
        change(() => {
          doc.source.kind = value;
        }),
    ),
    field(
      "Selected passage",
      doc.source.text,
      (value) =>
        change(() => {
          doc.source.text = value;
        }),
      "textarea",
      { rows: "9" },
    ),
    el(
      "p",
      { className: "muted" },
      doc.source.passage
        ? "Pinned SourceLens selection. Changing it requires an explicit rebind through the CLI."
        : "Supplied source snapshot. Version labels and interpretations are provided by the reader.",
    ),
    field("Source locator", doc.source.locator, (value) =>
      change(() => {
        doc.source.locator = value;
      }),
    ),
    field("Reference URL (optional HTTPS)", doc.source.reference_url, (value) =>
      change(() => {
        doc.source.reference_url = value || null;
      }),
    ),
  );
  $("readings").replaceChildren(
    ...doc.readings.map((r, index) => el("option", { value: index }, r.id)),
  );
  $("readings").value = String(state.selected);
  $("head").textContent = state.head
    ? "Head: " + state.head
    : "Not yet saved in this history.";
  renderReading();
  renderReview();
  renderAST();
  renderResults();
}
function renderReading() {
  const reading = selected();
  const row = (node) =>
    el(
      "tr",
      {},
      el(
        "td",
        {},
        field("Node ID " + node.id, node.id, (value) =>
          change(() => {
            node.id = value;
          }),
        ),
      ),
      el(
        "td",
        {},
        field("Formula " + node.id, node.formula, (value) =>
          change(() => {
            node.formula = value;
          }),
        ),
      ),
      el(
        "td",
        {},
        field("Speaker " + node.id, node.speaker, (value) =>
          change(() => {
            node.speaker = value;
          }),
        ),
        select(
          "Commitment " + node.id,
          node.commitment,
          [
            "user_assumption",
            "author_position",
            "opponent_position",
            "mutation_control",
            "hypothetical",
          ],
          (value) =>
            change(() => {
              node.commitment = value;
            }),
        ),
      ),
      el(
        "td",
        {},
        select(
          "Role " + node.id,
          node.role,
          ["premise", "inference"],
          (value) =>
            change(() => {
              node.role = value;
            }),
        ),
        field("Scope " + node.id, node.scope, (value) =>
          change(() => {
            node.scope = value;
          }),
        ),
      ),
      el(
        "td",
        {},
        field("Dependencies " + node.id, node.depends_on.join(", "), (value) =>
          change(() => {
            node.depends_on = value
              .split(",")
              .map((v) => v.trim())
              .filter(Boolean);
          }),
        ),
      ),
    );
  const table = el(
    "table",
    {},
    el(
      "thead",
      {},
      el(
        "tr",
        {},
        ["Node", "Formula", "Attribution", "Role / scope", "Uses"].map((name) =>
          el("th", {}, name),
        ),
      ),
    ),
    el("tbody", {}, reading.nodes.map(row)),
  );
  const addNode = (role) => {
    const prefix = role === "premise" ? "P" : "S";
    let n = 1;
    while (reading.nodes.some((node) => node.id === prefix + n)) n++;
    reading.nodes.push({
      id: prefix + n,
      formula: "p",
      role,
      speaker: "Reader",
      commitment: "user_assumption",
      scope: "Reader-added " + role,
      depends_on: [],
    });
    invalidate();
    renderReading();
  };
  const parts = [
    field("Reading ID", reading.id, (value) =>
      change(() => {
        reading.id = value;
        $("readings").options[state.selected].textContent = value;
      }),
    ),
    field(
      "Interpretation summary",
      reading.summary,
      (value) =>
        change(() => {
          reading.summary = value;
        }),
      "textarea",
      { rows: "2" },
    ),
    el(
      "div",
      { className: "two-fields" },
      select(
        "Claim origin",
        reading.origin,
        [
          "user_defined",
          "source_reconstruction",
          "deliberate_mutation_control",
        ],
        (value) =>
          change(() => {
            reading.origin = value;
          }),
      ),
      select(
        "Declared semantics",
        reading.semantics,
        ["classical-propositional-v1", "conceptual-case-review-v1"],
        (value) =>
          change(() => {
            reading.semantics = value;
          }),
      ),
    ),
    el("h3", {}, "Nodes and explicit dependencies"),
    el(
      "p",
      { className: "muted" },
      "Use ~, &, |, -> and <-> for supported formulas. Quoted words, beliefs and other operators need explicit semantic annotations.",
    ),
    el(
      "div",
      {
        className: "table-scroll",
        tabindex: "0",
        role: "region",
        "aria-label": "Node editor",
      },
      table,
    ),
    el(
      "div",
      { className: "node-actions" },
      el("button", { onclick: () => addNode("premise") }, "Add premise"),
      el("button", { onclick: () => addNode("inference") }, "Add inference"),
    ),
    select(
      "Selected target",
      reading.target,
      reading.nodes
        .filter((node) => node.role === "inference")
        .map((node) => node.id),
      (value) =>
        change(() => {
          reading.target = value;
        }),
    ),
  ];
  if (state.doc.schema_version === "reconstruction-v2") {
    const annotations = Object.fromEntries(
      ["senses", "occurrences", "qualifications", "bridges"].map((key) => [
        key,
        reading[key],
      ]),
    );
    const editor = field(
      "Semantic annotations JSON",
      JSON.stringify(annotations, null, 2),
      (value) => {
        try {
          const parsed = JSON.parse(value);
          if (
            Object.keys(parsed).sort().join() !==
            ["bridges", "occurrences", "qualifications", "senses"].join()
          )
            throw new Error(
              "Use exactly senses, occurrences, qualifications and bridges",
            );
          Object.assign(reading, parsed);
          state.annotationError = null;
          invalidate();
        } catch (error) {
          state.bundle = null;
          state.inspect = null;
          state.annotationError = error.message;
          state.generation++;
          message(error.message, true);
          renderReview();
          renderAST();
          renderResults();
        }
      },
      "textarea",
      { rows: "12", spellcheck: "false" },
    );
    parts.push(
      el(
        "details",
        {},
        el("summary", {}, "Advanced semantic annotations"),
        el(
          "p",
          { className: "muted" },
          "Offsets are Unicode code points within the selected passage. Sense IDs are proposed meanings, not proof of synonymy. Unencoded qualifications prevent execution.",
        ),
        editor,
      ),
    );
  }
  $("reading-editor").replaceChildren(...parts);
}
function renderReview() {
  const reading = state.doc ? selected() : null;
  if (!reading) return;
  const info = state.inspect?.readings[state.selected];
  const statuses = info?.review_status || {
    fidelity: "review_required",
    attribution: "review_required",
    interpretation: "review_required",
  };
  $("review-badges").replaceChildren(
    ...Object.entries(statuses).map(([name, value]) =>
      el("span", {}, name + " ", badge(value)),
    ),
  );
  const reviewAction = (aspect, decision) =>
    action(async () => {
      assertEditable();
      const generation = state.generation;
      const payload = {
        document: state.doc,
        reading_id: reading.id,
        aspect,
        decision,
        reviewer: $("reviewer").value,
        rationale: $("rationale").value,
      };
      const result = await api("/api/review", payload);
      if (generation !== state.generation) {
        message("Content changed; the in-flight review was not applied.");
        return;
      }
      state.doc = result.document;
      invalidate();
      render();
    });
  $("review-actions").replaceChildren(
    ...Object.entries(statuses).map(([aspect, value]) =>
      el(
        "div",
        { className: "review-card" },
        el("h4", {}, aspect),
        badge(value),
        el(
          "div",
          {},
          el(
            "button",
            {
              onclick: reviewAction(aspect, "self_reviewed"),
              disabled: !state.inspect,
            },
            "Record " + aspect + " review",
          ),
          el(
            "button",
            {
              onclick: reviewAction(aspect, "disputed"),
              disabled: !state.inspect,
            },
            "Dispute " + aspect,
          ),
        ),
      ),
    ),
  );
  $("review-history").replaceChildren(
    ...state.doc.reviews
      .filter((review) => review.reading_id === reading.id)
      .map((review) =>
        el(
          "div",
          { className: "history-record" },
          el("strong", {}, review.aspect + " · " + review.decision),
          el("p", {}, review.reviewer + ": " + review.rationale),
          el(
            "span",
            { className: "muted" },
            review.target_hash === info?.reading_hash
              ? "Matching content"
              : "Content hash: " + review.target_hash,
          ),
        ),
      ),
  );
}
function astTree(ast) {
  return el(
    "li",
    {},
    el("span", {}, ast.name || ast.op),
    ...(ast.args ? [el("ul", {}, ast.args.map(astTree))] : []),
  );
}
function renderAST() {
  const info = state.inspect?.readings[state.selected];
  $("asts").replaceChildren(
    ...(info?.asts || []).map((item) =>
      el(
        "details",
        { open: "" },
        el("summary", {}, item.id + ": " + item.formula),
        el("ul", { className: "ast-tree" }, astTree(item.ast)),
      ),
    ),
  );
}
function renderResults() {
  for (const id of ["verify-bundle", "export-bundle", "export-report"])
    $(id).disabled = !state.bundle;
  if (!state.bundle) {
    $("results-content").replaceChildren(
      el(
        "p",
        { className: "muted" },
        "No current result. Run a check after inspecting the source and review status.",
      ),
    );
    return;
  }
  $("results-content").replaceChildren(
    ...state.bundle.results.map((result) => {
      const outcome = result.analysis.result;
      const parts = [el("h3", {}, result.reading_id), badge(outcome.outcome)];
      if (result.semantic_scope)
        parts.push(
          el(
            "p",
            { className: "muted" },
            result.semantic_scope.level + ": " + result.semantic_scope.scope,
          ),
        );
      if (outcome.assignment)
        parts.push(
          el(
            "pre",
            { className: "witness" },
            JSON.stringify(outcome.assignment, null, 2),
          ),
          el("p", {}, "Witness rechecked against the original formulas."),
        );
      if (outcome.blocked_by)
        parts.push(el("p", {}, "Blocked by: " + outcome.blocked_by.join(", ")));
      const kernel = outcome.kernel_result;
      if (kernel?.encoding)
        parts.push(
          el(
            "p",
            { className: "muted" },
            "Largest CNF query: " +
              kernel.encoding.max_clauses +
              " clauses, " +
              kernel.encoding.max_variables +
              " variables including auxiliaries.",
          ),
        );
      parts.push(
        el(
          "p",
          { className: "muted" },
          "Backend: " +
            (kernel?.backend ||
              state.bundle.execution?.backend ||
              "exhaustive"),
        ),
      );
      if (kernel?.stats)
        parts.push(
          el("h4", {}, "Search observations"),
          el(
            "div",
            { className: "metrics" },
            Object.entries(kernel.stats).map(([key, value]) =>
              el("div", {}, el("strong", {}, value), el("span", {}, key)),
            ),
          ),
          el(
            "p",
            { className: "muted" },
            "Deterministic counters for this claim, including consistency checks. Not a general speedup estimate.",
          ),
        );
      parts.push(
        el(
          "details",
          {},
          el("summary", {}, "Complete calculation record"),
          el("pre", {}, JSON.stringify(result, null, 2)),
        ),
      );
      return el("article", { className: "result-card" }, parts);
    }),
  );
}
function tab(name) {
  for (const section of ["construct", "review", "results"])
    $(section).hidden = section !== name;
  document
    .querySelectorAll(".tabs button")
    .forEach((button) =>
      button.setAttribute("aria-pressed", String(button.dataset.tab === name)),
    );
}
function download(name, value, type = "application/json") {
  const blob = new Blob(
    [typeof value === "string" ? value : JSON.stringify(value, null, 2)],
    { type },
  );
  const url = URL.createObjectURL(blob);
  const anchor = el("a", { href: url, download: name });
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function refreshHeads() {
  const value = await api("/api/history");
  $("history").replaceChildren(
    el("option", { value: "" }, "Choose a saved head"),
    ...value.heads.map((head) =>
      el("option", { value: head.snapshot_hash }, head.id),
    ),
  );
}
document
  .querySelectorAll(".tabs button")
  .forEach((button) =>
    button.addEventListener("click", () => tab(button.dataset.tab)),
  );
$("readings").onchange = () => {
  state.selected = Number($("readings").value);
  renderReading();
  renderReview();
  renderAST();
};
$("load-example").onclick = action(async () => {
  openDocument(
    structuredClone(state.examples[Number($("examples").value)].document),
  );
  tab("construct");
});
$("add-reading").onclick = action(async () => {
  if (state.doc.readings.length >= 8)
    throw new Error("At most eight readings are supported");
  const reading = structuredClone(selected());
  let n = 1;
  while (state.doc.readings.some((r) => r.id === "alternative-" + n)) n++;
  reading.id = "alternative-" + n;
  state.doc.readings.push(reading);
  state.selected = state.doc.readings.length - 1;
  invalidate();
  render();
});
$("download-document").onclick = action(async () => {
  assertEditable();
  download("reconstruction.json", state.doc);
});
$("save").onclick = action(async () => {
  assertEditable();
  const generation = state.generation;
  const id = state.doc.reconstruction_id;
  const value = await api("/api/save", {
    document: state.doc,
    expected_head: state.head,
  });
  if (generation !== state.generation) {
    if (state.doc.reconstruction_id === id) {
      state.head = value.snapshot_hash;
      $("head").textContent = "Saved earlier snapshot; working edits retained.";
    }
    await refreshHeads();
    message("Earlier snapshot saved; current edits were retained.");
    return;
  }
  if (
    state.bundle &&
    state.doc.parent_revision !== value.document.parent_revision
  ) {
    state.bundle = null;
  }
  state.doc = value.document;
  state.head = value.snapshot_hash;
  render();
  await refreshHeads();
  message("Immutable snapshot saved. Old snapshots remain available.");
});
$("open-history").onclick = action(async () => {
  const hash = $("history").value;
  if (!hash) throw new Error("Choose a saved head first");
  const value = await api("/api/history/" + hash);
  openDocument(value.document, value.snapshot_hash);
});
$("import-file").onchange = action(async () => {
  const file = $("import-file").files[0];
  if (!file) return;
  if (file.size > 100000) throw new Error("Draft exceeds 100 KB");
  const value = await api("/api/import", {
    record: JSON.parse(await file.text()),
  });
  openDocument(value.document);
  message("Imported as a new draft; approval was not copied.");
});
$("analyze").onclick = action(async () => {
  assertEditable();
  $("analyze").disabled = true;
  message("Checking explicit claims…");
  const generation = state.generation;
  try {
    const bundle = await api("/api/analyze", {
      document: state.doc,
      backend: $("backend").value,
    });
    if (generation !== state.generation) {
      message("Content changed during calculation; result discarded.");
      return;
    }
    state.bundle = bundle;
    renderResults();
    tab("results");
    message(
      "Check completed. Inspect scope and verification before using the result.",
    );
  } finally {
    $("analyze").disabled = false;
  }
});
$("backend").onchange = () => invalidate();
$("verify-bundle").onclick = action(async () => {
  await api("/api/replay", { bundle: state.bundle });
  message("Result replay verified against recomputation.");
});
$("export-bundle").onclick = () => download("result-bundle.json", state.bundle);
$("export-report").onclick = action(async () => {
  const value = await api("/api/report", { bundle: state.bundle });
  download("review.html", value.html, "text/html");
  message("Verified read-only review report exported.");
});
$("bundle-file").onchange = action(async () => {
  const file = $("bundle-file").files[0];
  if (!file || file.size > 100000)
    throw new Error("Choose a bundle under 100 KB");
  await api("/api/replay", { bundle: JSON.parse(await file.text()) });
  message(
    "Imported bundle replay verified. The working draft was not changed.",
  );
});
let trajectoryPairs = [],
  trajectoryMeasurement = null;
function trajectoryPayload() {
  return {
    control_key: $("trajectory-control").value.trim(),
    treated_key: $("trajectory-treated").value.trim(),
    window: Number($("trajectory-window").value),
  };
}
function invalidateMeasurement() {
  trajectoryMeasurement = null;
  $("trajectory-export").disabled = true;
  $("trajectory-result").replaceChildren();
}
function chooseTrajectoryPair() {
  const pair = trajectoryPairs[Number($("trajectory-pairs").value)];
  if (!pair) return;
  $("trajectory-control").value = pair.control_key;
  $("trajectory-treated").value = pair.treated_key;
  invalidateMeasurement();
}
$("trajectory-pairs").onchange = chooseTrajectoryPair;
for (const name of [
  "trajectory-control",
  "trajectory-treated",
  "trajectory-window",
])
  $(name).addEventListener("input", invalidateMeasurement);
$("trajectory-report").onchange = action(async () => {
  const file = $("trajectory-report").files[0];
  if (!file || file.size > 2000000)
    throw new Error("Choose an experiment report under 2 MB");
  const report = JSON.parse(await file.text());
  if (report.experiment_version !== 3 || !Array.isArray(report.methods))
    throw new Error("Choose a trajectory-backed experiment-v3 report");
  const pairs = [];
  for (const method of report.methods) {
    for (const row of method.evaluated_candidates || [])
      for (const pair of row.trajectory_pairs || [])
        pairs.push({
          ...pair,
          label: `${method.method} · discovery · b=${row.b.toFixed(3)} p=${row.p.toFixed(3)} · seed ${pair.seed}`,
        });
    for (const pair of method.confirmation?.evidence_pairs || [])
      pairs.push({
        ...pair,
        label: `${method.method} · confirmation · seed ${pair.seed}`,
      });
  }
  if (
    !pairs.length ||
    pairs.length > 5000 ||
    pairs.some(
      (p) =>
        !/^[a-f0-9]{64}$/.test(p.control_key) ||
        !/^[a-f0-9]{64}$/.test(p.treated_key),
    )
  )
    throw new Error("The report must contain 1–5000 valid trajectory pairs");
  trajectoryPairs = pairs;
  $("trajectory-pairs").replaceChildren(
    ...pairs.map((pair, index) => el("option", { value: index }, pair.label)),
  );
  $("trajectory-window").value = report.config.final_window;
  chooseTrajectoryPair();
  message(
    "Experiment references loaded locally. Choose a pair and observation window.",
  );
});
function trajectoryChart(measurement) {
  const canvas = el("canvas", {
    role: "img",
    "aria-label":
      "Control and intervention cooperation fractions over time. Values range from zero to one.",
  });
  canvas.width = Math.max(
    280,
    Math.min(900, $("trajectory-result").clientWidth),
  );
  canvas.height = 240;
  const width = canvas.width;
  const ctx = canvas.getContext("2d");
  ctx.font = "12px system-ui";
  ctx.fillStyle = "#fbfcf8";
  ctx.fillRect(0, 0, width, 240);
  ctx.strokeStyle = "#d7e2d9";
  ctx.lineWidth = 1;
  for (const value of [0, 0.5, 1]) {
    const y = 200 - value * 160;
    ctx.beginPath();
    ctx.moveTo(45, y);
    ctx.lineTo(width - 20, y);
    ctx.stroke();
    ctx.fillStyle = "#637971";
    ctx.fillText(String(value), 10, y + 4);
  }
  for (const [series, color] of [
    [measurement.control_series, "#226e58"],
    [measurement.treated_series, "#bd7438"],
  ]) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    series.forEach((value, index) => {
      const x = 45 + (index / (series.length - 1)) * (width - 65),
        y = 200 - value * 160;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
  ctx.fillStyle = "#226e58";
  ctx.fillText("Control", 45, 22);
  ctx.fillStyle = "#bd7438";
  ctx.fillText("Intervention", 130, 22);
  ctx.fillStyle = "#637971";
  ctx.fillText("t = 0", 45, 225);
  ctx.fillText(
    "t = " + (measurement.control_series.length - 1),
    width - 85,
    225,
  );
  return canvas;
}
$("trajectory-measure").onclick = action(async () => {
  const payload = trajectoryPayload(),
    identity = JSON.stringify(payload);
  invalidateMeasurement();
  $("trajectory-measure").disabled = true;
  try {
    const result = await api("/api/measure", payload);
    if (JSON.stringify(trajectoryPayload()) !== identity) return;
    trajectoryMeasurement = result;
    const value = result.measurement;
    $("trajectory-result").replaceChildren(
      el("h3", {}, `Stored pair · ${result.backend} · seed ${value.seed}`),
      el(
        "p",
        {},
        `Final ${value.final_window} states: control ${value.control_mean.toFixed(6)}, intervention ${value.treated_mean.toFixed(6)}, effect ${value.effect.toFixed(6)}.`,
      ),
      trajectoryChart(value),
      el("p", { className: "muted" }, result.scope),
      el(
        "p",
        { className: "hash" },
        `Control: ${value.control_key} · Intervention: ${value.treated_key}`,
      ),
      el(
        "details",
        {},
        el("summary", {}, "Complete exploratory measurement"),
        el("pre", {}, JSON.stringify(result, null, 2)),
      ),
    );
    $("trajectory-export").disabled = false;
    message(
      "Stored trajectory identities, pairing and window arithmetic checked. No simulation was run.",
    );
  } finally {
    $("trajectory-measure").disabled = false;
  }
});
$("trajectory-export").onclick = () =>
  download("exploratory-measurement.json", trajectoryMeasurement);
window.addEventListener("resize", () => {
  const canvas = $("trajectory-result").querySelector("canvas");
  if (canvas && trajectoryMeasurement)
    canvas.replaceWith(trajectoryChart(trajectoryMeasurement.measurement));
});
(async () => {
  try {
    const value = await api("/api/examples");
    state.examples = value.examples;
    $("examples").replaceChildren(
      ...state.examples.map((example, index) =>
        el("option", { value: index }, example.name),
      ),
    );
    openDocument(structuredClone(state.examples[0].document));
    await refreshHeads();
    const health = await api("/api/health");
    $("trajectory-measure").disabled = !health.trajectory_store;
    $("trajectory-status").textContent = health.trajectory_store
      ? `Configured store: ${health.trajectory_store}. Use keys from an experiment report.`
      : "No trajectory store configured. Start with --trajectory-store kv --kv-port PORT --namespace NAME, or select a file/SQLite store.";
    $("connection").textContent = "Local · ready";
  } catch (error) {
    message(error.message, true);
    $("connection").textContent = "Connection error";
  }
})();
