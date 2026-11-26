import React, { useEffect, useState } from "react";
import Formula from "./Formula.jsx";

const CARD = { width: 304, height: 176, columnGap: 36, rowGap: 32, margin: 16 };

export default function ArgumentGraph({ reading, editable, onChange }) {
  const [selected, setSelected] = useState(reading?.target || "");
  useEffect(() => setSelected(reading?.target || ""), [reading?.id]);
  if (!reading) return null;
  const nodes = Object.fromEntries(reading.nodes.map((n) => [n.id, n]));
  const current = nodes[selected] || nodes[reading.target];
  const depths = {};
  function depth(id, active = new Set()) {
    if (depths[id] !== undefined) return depths[id];
    if (active.has(id) || !nodes[id]) return 0;
    const next = new Set([...active, id]);
    return (depths[id] = Math.min(
      19,
      nodes[id].depends_on.length
        ? 1 + Math.max(...nodes[id].depends_on.map((dep) => depth(dep, next)))
        : 0,
    ));
  }
  const rows = {},
    positions = {};
  for (const node of reading.nodes) {
    const col = depth(node.id),
      row = rows[col] || 0;
    rows[col] = row + 1;
    positions[node.id] = {
      x: CARD.margin + col * (CARD.width + CARD.columnGap),
      y: CARD.margin + row * (CARD.height + CARD.rowGap),
    };
  }
  const columns = Math.max(0, ...Object.values(depths)) + 1;
  const rowCount = Math.max(1, ...Object.values(rows));
  const width =
    CARD.margin * 2 + columns * CARD.width + (columns - 1) * CARD.columnGap;
  const height =
    CARD.margin * 2 + rowCount * CARD.height + (rowCount - 1) * CARD.rowGap;
  function changeNode(patch) {
    onChange({
      ...reading,
      nodes: reading.nodes.map((n) =>
        n.id === current.id ? { ...n, ...patch } : n,
      ),
    });
  }
  const formulas = reading.formal_mapping?.formulas || {};
  return (
    <section className="graph-editor" aria-label="Editable argument graph">
      <div className="graph-heading">
        <h3>Argument graph</h3>
        <details className="graph-guide">
          <summary>Graph guide</summary>
          <p>
            Select a node to edit its formula and dependencies. Incoming arrows
            form one joint premise set.
          </p>
        </details>
      </div>
      <div className="graph-canvas">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          style={{
            width,
            height,
          }}
          role="group"
          aria-label="Directed argument graph"
        >
          <defs>
            <marker
              id="argument-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#7e90a1" />
            </marker>
          </defs>
          {reading.nodes.flatMap((n) =>
            n.depends_on
              .filter((id) => positions[id])
              .map((id) => {
                const from = positions[id],
                  to = positions[n.id];
                const startX = from.x + CARD.width,
                  startY = from.y + CARD.height / 2,
                  endY = to.y + CARD.height / 2;
                return (
                  <path
                    key={`${id}-${n.id}`}
                    d={`M ${startX} ${startY} C ${startX + CARD.columnGap / 2} ${startY}, ${to.x - CARD.columnGap / 2} ${endY}, ${to.x} ${endY}`}
                    fill="none"
                    stroke="#7e90a1"
                    strokeWidth="2"
                    markerEnd="url(#argument-arrow)"
                  />
                );
              }),
          )}
          {reading.nodes.map((n) => {
            const { x, y } = positions[n.id];
            return (
              <g
                key={n.id}
                role="button"
                tabIndex={0}
                aria-label={`Select graph node ${n.id}`}
                onClick={() => setSelected(n.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelected(n.id);
                  }
                }}
                style={{ cursor: "pointer" }}
              >
                <title>{n.content}</title>
                <rect
                  x={x}
                  y={y}
                  width={CARD.width}
                  height={CARD.height}
                  rx="6"
                  fill={n.id === current?.id ? "#e9eff5" : "#fff"}
                  stroke={
                    n.commitment === "proposed_assumption"
                      ? "#b57b27"
                      : n.id === current?.id
                        ? "#315a79"
                        : "#cad3dc"
                  }
                  strokeWidth="1.5"
                />
                <text x={x + 12} y={y + 21} fontSize="13" fill="#253446">
                  {n.id} · {n.role}
                  {n.id === reading.target ? " · target" : ""}
                </text>
                <foreignObject
                  x={x + 12}
                  y={y + 32}
                  width={CARD.width - 24}
                  height={CARD.height - 44}
                >
                  <div className="graph-card-body">
                    <Formula
                      value={formulas[n.id]}
                      semantics={reading.formal_mapping?.semantics}
                    />
                    <p>{n.content}</p>
                  </div>
                </foreignObject>
              </g>
            );
          })}
        </svg>
      </div>
      {current && (
        <fieldset
          className="selected-node"
          key={current.id}
          disabled={!editable}
        >
          <legend>Selected node: {current.id}</legend>
          <p>{current.content}</p>
          <div
            className="selected-formula"
            aria-label={`Rendered formula ${current.id}`}
          >
            <Formula
              value={formulas[current.id]}
              semantics={reading.formal_mapping?.semantics}
            />
          </div>
          <details className="formula-input">
            <summary>Edit formula</summary>
            <label>
              Formula {current.id}
              <input
                aria-label={`Formula ${current.id}`}
                value={formulas[current.id] || ""}
                onChange={(e) =>
                  onChange({
                    ...reading,
                    formal_mapping: {
                      ...(reading.formal_mapping || {
                        rationale:
                          "Reader-defined propositional mapping; confirm symbol meanings before checking.",
                      }),
                      formulas: { ...formulas, [current.id]: e.target.value },
                    },
                  })
                }
              />
            </label>
          </details>
          <details className="structure-input">
            <summary>Edit argument structure</summary>
            <label>
              Node role
              <select
                aria-label={`Node role ${current.id}`}
                value={current.role}
                onChange={(e) =>
                  changeNode({
                    role: e.target.value,
                    depends_on:
                      e.target.value === "premise" ? [] : current.depends_on,
                  })
                }
              >
                <option value="premise">Premise</option>
                <option value="inference">Inference</option>
              </select>
            </label>
            <fieldset disabled={current.role !== "inference"}>
              <legend>Joint dependencies for {current.id}</legend>
              {reading.nodes
                .filter((n) => n.id !== current.id)
                .map((n) => (
                  <label className="checkbox" key={n.id}>
                    <input
                      type="checkbox"
                      aria-label={`Dependency ${n.id} into ${current.id}`}
                      checked={current.depends_on.includes(n.id)}
                      onChange={(e) =>
                        changeNode({
                          depends_on: e.target.checked
                            ? [...current.depends_on, n.id]
                            : current.depends_on.filter((id) => id !== n.id),
                        })
                      }
                    />
                    {n.id} · {n.content.slice(0, 70)}
                  </label>
                ))}
            </fieldset>
            <label>
              Target inference
              <select
                aria-label="Target inference"
                value={reading.target}
                onChange={(e) =>
                  onChange({ ...reading, target: e.target.value })
                }
              >
                {reading.nodes
                  .filter((n) => n.role === "inference")
                  .map((n) => (
                    <option key={n.id} value={n.id}>
                      {n.id}
                    </option>
                  ))}
              </select>
            </label>
          </details>
          {reading.formal_mapping && (
            <details className="mapping-input">
              <summary>Symbol meanings and scope</summary>
              <label>
                Symbol meanings and mapping scope
                <textarea
                  aria-label="Symbol meanings and mapping scope"
                  value={reading.formal_mapping.rationale}
                  onChange={(e) =>
                    onChange({
                      ...reading,
                      formal_mapping: {
                        ...reading.formal_mapping,
                        rationale: e.target.value,
                      },
                    })
                  }
                />
              </label>
            </details>
          )}
        </fieldset>
      )}
    </section>
  );
}
