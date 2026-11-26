import React, { useEffect, useState } from "react";
import { api } from "./client.js";

export default function VersionComparison({ run }) {
  const versions = run.versions || [];
  const [before, setBefore] = useState("");
  const [after, setAfter] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setBefore(versions[Math.max(0, versions.length - 2)]?.ref || "");
    setAfter(versions.at(-1)?.ref || "");
  }, [run.id, run.draft_ref, versions.length]);

  useEffect(() => {
    let active = true;
    setResult(null);
    setError("");
    if (before && after)
      api(
        `argument-runs/${run.id}/compare?before=${encodeURIComponent(before)}&after=${encodeURIComponent(after)}`,
      )
        .then((value) => {
          if (active) setResult(value);
        })
        .catch((caught) => {
          if (active) setError(caught.message);
        });
    return () => {
      active = false;
    };
  }, [run.id, before, after]);

  return (
    <details className="panel version-history">
      <summary>Saved versions and comparison ({versions.length})</summary>
      <p>
        Compare saved drafts. Local unsaved edits are excluded. Differences do not
        establish that a revision is better.
      </p>
      {versions.some((version) => version.origin === "legacy_current") && (
        <p>Older workflow: only the current saved draft is indexed here.</p>
      )}
      <ol>
        {versions.map((version, index) => (
          <li key={version.ref}>
            Version {index + 1} · {version.origin.replaceAll("_", " ")} {" "}
            {version.ref === run.draft_ref ? "· current" : ""}
            <small className="hash">{version.ref}</small>
          </li>
        ))}
      </ol>
      <div className="version-selectors">
        {[
          ["Compare from", before, setBefore],
          ["Compare to", after, setAfter],
        ].map(([label, value, setter]) => (
          <label key={label}>
            {label}
            <select
              aria-label={label}
              value={value}
              onChange={(event) => setter(event.target.value)}
            >
              {versions.map((version, index) => (
                <option key={version.ref} value={version.ref}>
                  Version {index + 1} · {version.origin.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      {error && <p role="alert">{error}</p>}
      {result && (
        <>
          <p aria-live="polite">
            {result.changes.length} changed fields or structures
          </p>
          {result.changes.map((change, index) => (
            <article className="version-change" key={index}>
              <h3>
                {change.path.join(" / ")} · {change.kind}
              </h3>
              <div className="version-columns">
                <div>
                  <strong>Before</strong>
                  <pre>{JSON.stringify(change.before, null, 2)}</pre>
                </div>
                <div>
                  <strong>After</strong>
                  <pre>{JSON.stringify(change.after, null, 2)}</pre>
                </div>
              </div>
            </article>
          ))}
          <details>
            <summary>Read both saved drafts</summary>
            <div className="version-columns">
              <pre>{JSON.stringify(result.before, null, 2)}</pre>
              <pre>{JSON.stringify(result.after, null, 2)}</pre>
            </div>
          </details>
        </>
      )}
    </details>
  );
}
