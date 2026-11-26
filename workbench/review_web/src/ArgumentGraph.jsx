import React, {useEffect, useState} from "react";

export default function ArgumentGraph({reading, editable, onChange}) {
  const [selected, setSelected] = useState(reading?.target || "");
  useEffect(() => setSelected(reading?.target || ""), [reading?.id]);
  if (!reading) return null;
  const nodes = Object.fromEntries(reading.nodes.map(n => [n.id, n]));
  const current = nodes[selected] || nodes[reading.target];
  const depths = {};
  function depth(id, active = new Set()) {
    if (depths[id] !== undefined) return depths[id];
    if (active.has(id) || !nodes[id]) return 0;
    const next = new Set([...active, id]);
    return depths[id] = Math.min(19, nodes[id].depends_on.length
      ? 1 + Math.max(...nodes[id].depends_on.map(dep => depth(dep, next))) : 0);
  }
  const rows = {}, positions = {};
  for (const node of reading.nodes) {
    const col = depth(node.id), row = rows[col] || 0;
    rows[col] = row + 1; positions[node.id] = {x: 20 + col * 240, y: 20 + row * 110};
  }
  const width = 240 * (Math.max(0, ...Object.values(depths)) + 1);
  const height = 110 * Math.max(1, ...Object.values(rows));
  function changeNode(patch) {
    onChange({...reading, nodes: reading.nodes.map(n => n.id === current.id ? {...n, ...patch} : n)});
  }
  const formulas = reading.formal_mapping?.formulas || {};
  return <section className="graph-editor" aria-label="Editable argument graph">
    <h3>Argument graph</h3>
    <p>Select a node to edit its formula and incoming dependencies. Incoming arrows form one joint premise set; they do not assert that each premise alone entails the conclusion.</p>
    <div style={{overflowX: "auto", maxWidth: "100%"}}>
      <svg viewBox={`0 0 ${width} ${height}`} style={{width: "100%", minWidth: 350, minHeight: 180}} role="group" aria-label="Directed argument graph">
        <defs><marker id="argument-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#58776f" /></marker></defs>
        {reading.nodes.flatMap(n => n.depends_on.filter(id => positions[id]).map(id => {
          const from = positions[id], to = positions[n.id];
          return <path key={`${id}-${n.id}`} d={`M ${from.x + 200} ${from.y + 35} C ${from.x + 220} ${from.y + 35}, ${to.x - 20} ${to.y + 35}, ${to.x} ${to.y + 35}`} fill="none" stroke="#58776f" strokeWidth="2" markerEnd="url(#argument-arrow)" />;
        }))}
        {reading.nodes.map(n => {
          const {x, y} = positions[n.id];
          return <g key={n.id} role="button" tabIndex={0} aria-label={`Select graph node ${n.id}`}
            onClick={() => setSelected(n.id)} onKeyDown={e => {if (e.key === "Enter" || e.key === " ") {e.preventDefault(); setSelected(n.id);}}} style={{cursor:"pointer"}}>
            <rect x={x} y={y} width="200" height="80" rx="8" fill={n.id === current?.id ? "#d9eee6" : "#fff"} stroke={n.commitment === "proposed_assumption" ? "#b57b27" : "#58776f"} strokeWidth="2" />
            <text x={x+10} y={y+20} fontSize="13" fill="#173b35">{n.id} · {n.role}{n.id === reading.target ? " · target" : ""}</text>
            <text x={x+10} y={y+42} fontSize="12" fill="#173b35">{(formulas[n.id] || "No formula").slice(0,25)}</text>
            <text x={x+10} y={y+63} fontSize="10" fill="#58776f">{n.content.slice(0,30)}{n.content.length > 30 ? "…" : ""}</text>
          </g>;
        })}
      </svg>
    </div>
    {current && <fieldset disabled={!editable}>
      <legend>Selected node: {current.id}</legend>
      <p>{current.content}</p>
      <label>Formula {current.id}<input aria-label={`Formula ${current.id}`} value={formulas[current.id] || ""}
        onChange={e => onChange({...reading, formal_mapping: {...(reading.formal_mapping || {rationale: "Reader-defined propositional mapping; confirm symbol meanings before checking."}), formulas: {...formulas, [current.id]: e.target.value}}})} /></label>
      <label>Node role<select aria-label={`Node role ${current.id}`} value={current.role}
        onChange={e => changeNode({role: e.target.value, depends_on: e.target.value === "premise" ? [] : current.depends_on})}>
        <option value="premise">Premise</option><option value="inference">Inference</option>
      </select></label>
      <fieldset disabled={current.role !== "inference"}><legend>Joint dependencies for {current.id}</legend>
        {reading.nodes.filter(n => n.id !== current.id).map(n => <label className="checkbox" key={n.id}>
          <input type="checkbox" aria-label={`Dependency ${n.id} into ${current.id}`} checked={current.depends_on.includes(n.id)}
            onChange={e => changeNode({depends_on: e.target.checked ? [...current.depends_on, n.id] : current.depends_on.filter(id => id !== n.id)})} />{n.id} · {n.content.slice(0,70)}
        </label>)}
      </fieldset>
      <label>Target inference<select aria-label="Target inference" value={reading.target} onChange={e => onChange({...reading, target:e.target.value})}>
        {reading.nodes.filter(n => n.role === "inference").map(n => <option key={n.id} value={n.id}>{n.id}</option>)}
      </select></label>
      {reading.formal_mapping && <label>Symbol meanings and mapping scope<textarea aria-label="Symbol meanings and mapping scope" value={reading.formal_mapping.rationale}
        onChange={e => onChange({...reading, formal_mapping: {...reading.formal_mapping, rationale:e.target.value}})} /></label>}
    </fieldset>}
  </section>;
}
