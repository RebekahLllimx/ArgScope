import React, { useEffect, useRef, useState } from "react";
import { api, json } from "../api";

export default function ImportDialog({ initial, onClose, onSaved, onError }) {
  const [mode, setMode] = useState("file");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current.querySelector('input[name="source_key"]').focus();
    return () => previous?.focus();
  }, []);
  function keepFocus(event) {
    if (event.key !== "Tab") return;
    const controls = Array.from(
      dialog.current.querySelectorAll(
        "button, input, textarea, select, a[href]",
      ),
    ).filter((element) => !element.disabled);
    const first = controls[0],
      last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    const fields = new FormData(event.target);
    try {
      if (mode === "file")
        await api("/documents/upload", { method: "POST", body: fields });
      else await api("/documents", json(Object.fromEntries(fields)));
      await onSaved();
    } catch (err) {
      setError(err.message);
      onError("");
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <section
        className="modal"
        ref={dialog}
        onKeyDown={keepFocus}
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-title"
      >
        <div className="drawer-top">
          <h2 id="import-title">Import a document</h2>
          <button className="text-button" onClick={onClose}>
            Close
          </button>
        </div>
        <form onSubmit={save}>
          <label>
            Document key
            <input
              name="source_key"
              pattern="[a-zA-Z0-9][a-zA-Z0-9_.-]*"
              required
              maxLength="100"
              defaultValue={initial.source_key || ""}
              placeholder="workflow-notes"
            />
          </label>
          <label>
            Title
            <input
              name="title"
              required
              maxLength="180"
              defaultValue={initial.title || ""}
            />
          </label>
          <div className="form-pair">
            <label>
              Version label
              <input
                name="version_label"
                required
                maxLength="80"
                defaultValue={initial.version_label ?? "v1"}
              />
            </label>
            <label>
              Source URL · optional
              <input name="source_url" type="url" />
            </label>
          </div>
          <div className="mode-switch">
            <button
              type="button"
              className={mode === "file" ? "selected" : ""}
              onClick={() => setMode("file")}
            >
              Upload file
            </button>
            <button
              type="button"
              className={mode === "text" ? "selected" : ""}
              onClick={() => setMode("text")}
            >
              Paste Markdown
            </button>
          </div>
          {mode === "file" ? (
            <label>
              Markdown, text or text-based PDF · up to 5 MB
              <input
                type="file"
                name="file"
                accept=".md,.markdown,.txt,.pdf"
                required
              />
            </label>
          ) : (
            <label>
              Document text
              <textarea name="text" rows="7" required />
            </label>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div className="modal-actions">
            <button
              type="button"
              className="button secondary"
              onClick={onClose}
            >
              Cancel
            </button>
            <button className="button primary" disabled={saving}>
              {saving ? "Importing…" : "Import document"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
