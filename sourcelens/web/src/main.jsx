import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

import { api, json, METHODS } from "./api";
import ImportDialog from "./components/ImportDialog";

const STAGES = ["plan", "retrieve", "assess", "compose", "done"];
const EMBEDDED = window.location.pathname === "/library";

function App() {
  const [documents, setDocuments] = useState([]);
  const [runs, setRuns] = useState([]);
  const [tab, setTab] = useState("research");
  const [question, setQuestion] = useState(
    "How do write-ahead logs and checkpoints handle recovery after a crash?",
  );
  const [method, setMethod] = useState("hybrid");
  const [job, setJob] = useState(null);
  const [search, setSearch] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [importing, setImporting] = useState(null);
  const [evidence, setEvidence] = useState(null);
  const [versions, setVersions] = useState(null);
  const events = useRef(null);
  const jobGeneration = useRef(0);
  const report = job?.state?.report;
  const stagePosition =
    job?.state.next_node === "follow_up"
      ? 2
      : STAGES.indexOf(job?.state.next_node);

  async function refresh() {
    const [docs, history] = await Promise.all([
      api("/documents"),
      api("/runs"),
    ]);
    setDocuments(docs);
    setRuns(history);
  }
  useEffect(() => {
    refresh().catch((err) => setError(err.message));
    return () => events.current?.close();
  }, []);
  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") {
        setImporting(null);
        setEvidence(null);
        setVersions(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function loadJob(id, watch = true) {
    const generation = ++jobGeneration.current;
    events.current?.close();
    const next = await api("/runs/" + id);
    if (generation !== jobGeneration.current) return;
    const accept = (value) => {
      if (generation !== jobGeneration.current) return;
      setJob((previous) =>
        previous?.id === value.id && previous.updated_at > value.updated_at
          ? previous
          : value,
      );
    };
    setJob(next);
    setTab("research");
    if (watch && ["queued", "running"].includes(next.status)) {
      const source = new EventSource("/api/runs/" + id + "/events");
      events.current = source;
      source.addEventListener("progress", () =>
        api("/runs/" + id)
          .then(accept)
          .catch((err) => setError(err.message)),
      );
      source.addEventListener("done", () => {
        source.close();
        api("/runs/" + id)
          .then(accept)
          .catch((err) => setError(err.message));
        refresh().catch((err) => setError(err.message));
      });
      source.onerror = () => {
        // EventSource reconnects with Last-Event-ID; persisted events are replayable.
        api("/runs/" + id)
          .then((value) => {
            accept(value);
            if (["succeeded", "failed"].includes(value.status)) source.close();
          })
          .catch((err) => setError(err.message));
      };
    }
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    setEvidence(null);
    try {
      if (tab === "search") {
        setSearch(
          await api("/search", json({ query: question, method, limit: 8 })),
        );
      } else {
        const request = json({ question, method });
        request.headers["Idempotency-Key"] = crypto.randomUUID();
        const next = await api("/runs", request);
        await loadJob(next.id);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function openEvidence(id) {
    try {
      setEvidence(await api("/chunks/" + id));
    } catch (err) {
      setError(err.message);
    }
  }
  async function showVersions(document) {
    try {
      setVersions({
        document,
        rows: await api("/documents/" + document.id + "/versions"),
      });
    } catch (err) {
      setError(err.message);
    }
  }
  async function loadExamples() {
    setBusy(true);
    try {
      await api("/examples", { method: "POST" });
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a
          className="brand"
          href="/"
          aria-label={EMBEDDED ? "ArgScope home" : "SourceLens home"}
        >
          <span className="brand-mark" aria-hidden="true">
            {EMBEDDED ? "A" : "S"}
          </span>
          {EMBEDDED ? "ArgScope" : "SourceLens"}
        </a>
        <nav className="app-links" aria-label="Application">
          {EMBEDDED && <a href="/">Arguments</a>}
          <a
            href={EMBEDDED ? "/library" : "/"}
            aria-label="Source library"
            aria-current="page"
          >
            Sources
          </a>
        </nav>
      </header>
      <div className="workspace">
        <aside className="library">
          <div className="section-heading">
            <h2>Documents</h2>
            <span className="count">{documents.length}</span>
          </div>
          <button
            className="button secondary full"
            onClick={() => setImporting({})}
          >
            Import document
          </button>
          <div className="document-list">
            {documents.map((document) => (
              <article className="document" key={document.id}>
                <button
                  className="document-title"
                  onClick={() => showVersions(document)}
                >
                  {document.title}
                </button>
                <p>{document.label}</p>
                <div className="document-meta">
                  <span>{document.chunk_count} passages</span>
                  <button onClick={() => showVersions(document)}>
                    {document.version_count}{" "}
                    {document.version_count === 1 ? "version" : "versions"}
                  </button>
                </div>
              </article>
            ))}
          </div>
          {!documents.length && (
            <div className="library-empty">
              <p>No documents yet.</p>
              <button
                className="text-button"
                disabled={busy}
                onClick={loadExamples}
              >
                Load example library
              </button>
            </div>
          )}
        </aside>
        <main>
          <nav className="tabs" aria-label="Workspace views">
            {["research", "search", "history"].map((value) => (
              <button
                key={value}
                aria-current={tab === value ? "page" : undefined}
                className={tab === value ? "active" : ""}
                onClick={() => setTab(value)}
              >
                {value === "history"
                  ? "Recent runs"
                  : value === "search"
                    ? "Search passages"
                    : "Research"}
              </button>
            ))}
          </nav>
          {error && (
            <div className="error" role="alert">
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                Dismiss
              </button>
            </div>
          )}
          {tab !== "history" ? (
            <>
              <div className="intro">
                <h1>
                  {tab === "research"
                    ? "Search your sources"
                    : "Search passages"}
                </h1>
              </div>
              <form className="question-form" onSubmit={submit}>
                <label htmlFor="question">
                  {tab === "search" ? "Search query" : "Research question"}
                </label>
                <textarea
                  id="question"
                  value={question}
                  minLength={5}
                  maxLength={600}
                  required
                  onChange={(event) => setQuestion(event.target.value)}
                  rows="3"
                />
                <div className="form-actions">
                  <label className="method-label">
                    Retrieval
                    <select
                      value={method}
                      onChange={(event) => setMethod(event.target.value)}
                    >
                      {Object.entries(METHODS).map(([key, label]) => (
                        <option value={key} key={key}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    className="button primary"
                    disabled={busy || !documents.length}
                  >
                    {busy
                      ? "Starting…"
                      : tab === "search"
                        ? "Search library"
                        : "Start research"}
                  </button>
                </div>
              </form>
              {tab === "research" && job && (
                <section className="run" aria-live="polite">
                  <div className="run-top">
                    <span className={"status " + job.status}>{job.status}</span>
                    <span>
                      {job.state.snapshot.length} pinned source versions
                    </span>
                    {job.status === "succeeded" && (
                      <a
                        href={"/api/runs/" + job.id + "/report.md"}
                        className="text-button"
                      >
                        Download Markdown
                      </a>
                    )}
                  </div>
                  <ol className="progress" aria-label="Research progress">
                    {STAGES.map((stage, index) => (
                      <li
                        key={stage}
                        className={stagePosition >= index ? "reached" : ""}
                      >
                        {stage === "done"
                          ? "Report"
                          : stage === "compose"
                            ? "Collect excerpts"
                            : stage[0].toUpperCase() + stage.slice(1)}
                      </li>
                    ))}
                  </ol>
                  {job.status === "failed" && (
                    <div className="error">
                      <p>{job.error}</p>
                      <button
                        onClick={async () => {
                          try {
                            await api("/runs/" + job.id + "/retry", {
                              method: "POST",
                            });
                            await loadJob(job.id);
                          } catch (err) {
                            setError(err.message);
                          }
                        }}
                      >
                        Retry run
                      </button>
                    </div>
                  )}
                  {report && (
                    <>
                      <div className="report-heading">
                        <h2>{report.question}</h2>
                        {["insufficient_evidence", "partial_evidence"].includes(
                          report.status,
                        ) && (
                          <p>
                            {report.status === "insufficient_evidence"
                              ? "No matching evidence was found in this library snapshot."
                              : report.status === "partial_evidence"
                                ? "Evidence found for some parts of the question."
                                : null}
                          </p>
                        )}
                      </div>
                      {report.outdated_sources > 0 && (
                        <div className="notice">
                          {report.outdated_sources} cited{" "}
                          {report.outdated_sources === 1
                            ? "source has"
                            : "sources have"}{" "}
                          a newer version. This report preserves its original
                          evidence.
                        </div>
                      )}
                      {report.sections.map((section, index) => (
                        <section className="evidence-section" key={index}>
                          {report.sections.length > 1 && (
                            <h3>{section.question}</h3>
                          )}
                          {!section.quotes.length &&
                            report.sections.length > 1 && (
                              <p className="muted">
                                No matching passage for this part of the
                                question.
                              </p>
                            )}
                          <div className="evidence-grid">
                            {section.quotes
                              .filter(
                                (quote, position, quotes) =>
                                  quotes.findIndex(
                                    (item) => item.chunk_id === quote.chunk_id,
                                  ) === position,
                              )
                              .map((quote, position) => {
                                const cite = report.citations.find(
                                  (item) => item.id === quote.chunk_id,
                                );
                                return (
                                  <article
                                    className="quote-card"
                                    key={position}
                                  >
                                    <div className="quote-heading">
                                      <span>{cite.heading}</span>
                                      <button
                                        className="citation"
                                        onClick={() =>
                                          openEvidence(quote.chunk_id)
                                        }
                                      >
                                        {quote.citation}
                                      </button>
                                    </div>
                                    {section.quotes
                                      .filter(
                                        (item) =>
                                          item.chunk_id === quote.chunk_id,
                                      )
                                      .map((item, quoteIndex) => (
                                        <blockquote key={quoteIndex}>
                                          {item.text}
                                        </blockquote>
                                      ))}
                                    <button
                                      className="source-link"
                                      onClick={() =>
                                        openEvidence(quote.chunk_id)
                                      }
                                    >
                                      {cite.title}
                                    </button>
                                    <p className="quote-meta">
                                      {cite.label} ·{" "}
                                      {cite.page
                                        ? "Page " + cite.page
                                        : "Lines " +
                                          cite.line_start +
                                          "–" +
                                          cite.line_end}
                                      {!cite.is_current &&
                                        " · Newer version available"}
                                    </p>
                                  </article>
                                );
                              })}
                          </div>
                        </section>
                      ))}
                      {!report.citations.length && (
                        <p className="report-note">
                          Add a relevant source or try different search terms.
                        </p>
                      )}
                    </>
                  )}
                </section>
              )}
              {tab === "search" && search && (
                <section className="search-results" aria-live="polite">
                  <h2>{search.length} matching passages</h2>
                  {search.length === 0 && (
                    <p>No passages found. Try different technical terms.</p>
                  )}
                  {search.map((chunk, index) => (
                    <article className="search-card" key={chunk.id}>
                      <div className="quote-heading">
                        <strong>{chunk.heading}</strong>
                        <span className="rank">Rank {index + 1}</span>
                      </div>
                      <p>{chunk.body}</p>
                      <button
                        className="source-link"
                        onClick={() => openEvidence(chunk.id)}
                      >
                        {chunk.title} · {chunk.label}
                      </button>
                      <p className="quote-meta">
                        {chunk.bm25_rank && "BM25 rank " + chunk.bm25_rank}
                        {chunk.vector_rank &&
                          (chunk.bm25_rank ? " · " : "") +
                            "Vector rank " +
                            chunk.vector_rank}
                      </p>
                    </article>
                  ))}
                </section>
              )}
            </>
          ) : (
            <section className="history">
              <div className="intro">
                <h1>Recent research</h1>
              </div>
              {!runs.length && <p>No research runs yet.</p>}
              {runs.map((run) => (
                <button
                  className="history-row"
                  key={run.id}
                  onClick={() =>
                    loadJob(run.id).catch((err) => setError(err.message))
                  }
                >
                  <span>
                    <strong>{run.question}</strong>
                    <small>
                      {METHODS[run.method]} ·{" "}
                      {new Date(run.created_at * 1000).toLocaleString()}
                    </small>
                  </span>
                  <span className={"status " + run.status}>{run.status}</span>
                </button>
              ))}
            </section>
          )}
        </main>
      </div>
      {importing && (
        <ImportDialog
          initial={importing}
          onClose={() => setImporting(null)}
          onSaved={async () => {
            setImporting(null);
            await refresh();
            if (job) await loadJob(job.id);
          }}
          onError={setError}
        />
      )}
      {evidence && (
        <aside className="drawer" aria-label="Source passage">
          <div className="drawer-top">
            <p className="eyebrow">Source passage</p>
            <button className="text-button" onClick={() => setEvidence(null)}>
              Close
            </button>
          </div>
          <h2>{evidence.title}</h2>
          <p className="muted">
            {evidence.label} ·{" "}
            {evidence.is_current ? "Current version" : "Earlier version"}
          </p>
          <h3>{evidence.heading}</h3>
          <p className="source-body">{evidence.body}</p>
          <dl>
            <dt>Location</dt>
            <dd>
              {evidence.page
                ? "Page " + evidence.page
                : "Lines " + evidence.line_start + "–" + evidence.line_end}
            </dd>
          </dl>
          <details>
            <summary>Technical identifiers</summary>
            <dl>
              <dt>Content SHA-256</dt>
              <dd className="digest">{evidence.digest}</dd>
              <dt>Chunk identifier</dt>
              <dd className="digest">{evidence.id}</dd>
            </dl>
          </details>
          {evidence.source_url && (
            <a href={evidence.source_url} target="_blank" rel="noreferrer">
              Open source link
            </a>
          )}
        </aside>
      )}
      {versions && (
        <aside className="drawer" aria-label="Document versions">
          <div className="drawer-top">
            <p className="eyebrow">Version history</p>
            <button className="text-button" onClick={() => setVersions(null)}>
              Close
            </button>
          </div>
          <h2>{versions.document.title}</h2>
          <details>
            <summary>Document identifier</summary>
            <code className="digest">{versions.document.source_key}</code>
          </details>
          <button
            className="button secondary"
            onClick={() => {
              setImporting({
                source_key: versions.document.source_key,
                title: versions.document.title,
                version_label: "",
              });
              setVersions(null);
            }}
          >
            Import new version
          </button>
          {versions.rows.map((row) => (
            <article className="version" key={row.id}>
              <h3>{row.label}</h3>
              <p>
                {row.is_current ? "Current" : "Earlier version"} ·{" "}
                {new Date(row.created_at * 1000).toLocaleString()}
              </p>
              <details>
                <summary>Identifier</summary>
                <code className="digest">{row.digest}</code>
              </details>
            </article>
          ))}
        </aside>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
