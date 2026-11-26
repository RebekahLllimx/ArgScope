import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import ArgumentGraph from "./ArgumentGraph.jsx";
import VersionComparison from "./VersionComparison.jsx";
import { api, download } from "./client.js";
const clone = (value) => structuredClone(value);
function emptyRequest() {
  return {
    question: "",
    sources: [
      {
        id: "main",
        title: "",
        version: "v1",
        text: "",
        kind: "user_text",
        reference_url: null,
        binding: null,
      },
    ],
    notes: "",
  };
}
const labels = {
  queued: "Queued",
  running: "Review in progress",
  awaiting_review: "Your review is needed",
  succeeded: "Report ready",
  failed: "Run needs attention",
  interrupted: "Ready to resume",
  budget_exhausted: "Budget reached",
  cancelled: "Cancelled",
};
function Badge({ children, tone = "" }) {
  return <span className={"badge " + tone}>{children}</span>;
}
function App() {
  const [health, setHealth] = useState(null),
    [examples, setExamples] = useState([]),
    [example, setExample] = useState("");
  const [request, setRequest] = useState(emptyRequest),
    [runs, setRuns] = useState([]),
    [run, setRun] = useState(null),
    [page, setPage] = useState("new");
  const [draft, setDraft] = useState(null),
    [readingIndex, setReadingIndex] = useState(0),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const [review, setReview] = useState({
    reviewer: "",
    rationale: "",
    fidelity: "",
    attribution: "",
    interpretation: "",
    approve_formal_mapping: false,
    approve_formula_scope: false,
  });
  const [workflowMode, setWorkflowMode] = useState("single_draft");
  const [formResetKey, setFormResetKey] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(
    () => window.matchMedia("(min-width: 761px)").matches,
  );
  const [feedbackText, setFeedbackText] = useState(""),
    [feedbackReviewer, setFeedbackReviewer] = useState("");
  const [library, setLibrary] = useState([]),
    [libraryChoice, setLibraryChoice] = useState(""),
    [jsonText, setJsonText] = useState(""),
    [jsonError, setJsonError] = useState(""),
    [jsonDirty, setJsonDirty] = useState(false);
  const stream = useRef(null),
    generation = useRef(0),
    currentId = useRef(null);
  const act = (fn) => async () => {
    try {
      setMessage("");
      await fn();
    } catch (error) {
      setMessage(error.message);
    }
  };
  const refreshRuns = async () => setRuns(await api("argument-runs"));
  function watch(id) {
    stream.current?.close();
    const token = ++generation.current;
    currentId.current = id;
    let requestSequence = 0;
    const fetchRun = async () => {
      const requestToken = ++requestSequence;
      const value = await api("argument-runs/" + id);
      // An older progress fetch can finish after the final settled fetch.
      if (token === generation.current && requestToken === requestSequence) {
        setRun(value);
        setRuns((previous) =>
          previous.map((item) =>
            item.id === id ? { ...item, status: value.status } : item,
          ),
        );
      }
      return value;
    };
    fetchRun()
      .then((value) => {
        if (
          token !== generation.current ||
          !["running", "queued"].includes(value.status)
        )
          return;
        const source = new EventSource("/api/argument-runs/" + id + "/events");
        stream.current = source;
        source.addEventListener("progress", () =>
          fetchRun().catch((e) => setMessage(e.message)),
        );
        source.addEventListener("settled", () => {
          source.close();
          fetchRun()
            .then(() => refreshRuns())
            .catch((e) => setMessage(e.message));
        });
        source.onerror = () => {
          fetchRun()
            .then((value) => {
              if (!["running", "queued"].includes(value.status)) {
                source.close();
                refreshRuns();
              }
            })
            .catch((e) => setMessage(e.message));
        };
      })
      .catch((e) => setMessage(e.message));
  }
  useEffect(() => {
    (async () => {
      try {
        const [h, e] = await Promise.all([
          api("argument-health"),
          api("argument-examples"),
        ]);
        setHealth(h);
        setExamples(e);
        await refreshRuns();
      } catch (error) {
        setMessage(error.message);
      }
    })();
    return () => stream.current?.close();
  }, []);
  useEffect(() => {
    const viewport = window.matchMedia("(min-width: 761px)");
    const syncHistory = () => setHistoryOpen(viewport.matches);
    viewport.addEventListener("change", syncHistory);
    return () => viewport.removeEventListener("change", syncHistory);
  }, []);
  useEffect(() => {
    if (run?.draft) {
      setDraft(clone(run.draft));
      setReadingIndex(0);
      setJsonText(JSON.stringify(run.draft, null, 2));
      setJsonError("");
      setJsonDirty(false);
      setReview({
        reviewer: "",
        rationale: "",
        fidelity: "",
        attribution: "",
        interpretation: "",
        approve_formal_mapping: false,
        approve_formula_scope: false,
      });
    }
    setFeedbackText("");
    setFeedbackReviewer("");
  }, [run?.id, run?.draft_ref]);
  useEffect(() => {
    if (
      draft &&
      run?.draft &&
      JSON.stringify(draft) !== JSON.stringify(run.draft)
    )
      setReview((previous) => ({
        ...previous,
        fidelity: "",
        attribution: "",
        interpretation: "",
        approve_formal_mapping: false,
        approve_formula_scope: false,
      }));
  }, [draft]);
  const running = run && ["running", "queued"].includes(run.status),
    editable = run?.status === "awaiting_review";
  const reading = draft?.readings[readingIndex];
  function editNode(index, key, value) {
    setDraft((previous) => {
      const next = clone(previous);
      next.readings[readingIndex].nodes[index][key] = value;
      return next;
    });
    setJsonText("");
    setJsonDirty(false);
    setJsonError("");
  }
  function newReview() {
    stream.current?.close();
    generation.current++;
    setRun(null);
    setDraft(null);
    setRequest(emptyRequest());
    setExample("");
    setLibrary([]);
    setLibraryChoice("");
    setWorkflowMode("single_draft");
    setFormResetKey((key) => key + 1);
    setPage("new");
    setMessage("");
  }
  const start = async (event) => {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const created = await api("argument-runs", {
        ...request,
        workflow_mode: workflowMode,
      });
      setPage("workspace");
      watch(created.id);
      await refreshRuns();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };
  const finish = async (event) => {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      if (jsonError || jsonDirty)
        throw new Error("Fix the advanced draft JSON before continuing.");
      const edited =
        JSON.stringify(draft) === JSON.stringify(run.draft) ? null : draft;
      await api("argument-runs/" + run.id + "/resume", {
        ...review,
        approve_formal_mapping:
          review.approve_formal_mapping &&
          (review.approve_formula_scope ||
            ["fidelity", "attribution", "interpretation"].every(
              (k) => review[k] === "accepted",
            )),
        expected_draft: run.draft_ref,
        edited_draft: edited,
      });
      watch(run.id);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };
  const requestRevision = async (event) => {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      if (jsonDirty || jsonError)
        throw new Error("Apply valid JSON changes before requesting revision.");
      await api(`argument-runs/${run.id}/feedback`, {
        action: "request_revision",
        expected_draft: run.draft_ref,
        reviewer: feedbackReviewer,
        feedback: feedbackText,
        edited_draft:
          JSON.stringify(draft) === JSON.stringify(run.draft) ? null : draft,
      });
      setReview({
        reviewer: "",
        rationale: "",
        fidelity: "",
        attribution: "",
        interpretation: "",
        approve_formal_mapping: false,
        approve_formula_scope: false,
      });
      watch(run.id);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  };
  const selectLibrary = async () => {
    setLibrary(await api("argument-sources"));
    setLibraryChoice("");
  };
  const useLibrary = () => {
    const source = library.find((s) => s.chunk_id === libraryChoice);
    if (!source) return;
    setRequest({
      ...request,
      sources: [
        {
          id: "main",
          title: source.title,
          version: source.version,
          text: source.text,
          kind: "user_text",
          reference_url: source.reference_url,
          binding: source.binding,
        },
      ],
    });
  };
  const exportReport = async (type) => {
    if (type === "json")
      download(
        "argument-review.json",
        await api("argument-runs/" + run.id + "/report.json"),
      );
    else {
      const response = await fetch(
        "/api/argument-runs/" + run.id + "/report.html",
      );
      if (!response.ok) throw new Error("Report verification failed.");
      download("argument-review.html", await response.text(), "text/html");
    }
  };
  const source = request?.sources[0];
  const sourceWordCount = source?.text.trim()
    ? source.text.trim().split(/\s+/).length
    : 0;
  const canStart = Boolean(
    source?.title.trim() &&
    source?.text.trim() &&
    request?.question.trim().length >= 5,
  );
  return (
    <>
      <a className="skip" href="#main">
        Skip to workspace
      </a>
      <header>
        <a href="/" className="brand">
          <span className="brand-mark" aria-hidden="true">
            A
          </span>
          <span>ArgScope</span>
        </a>
        <nav className="app-links" aria-label="Application">
          <a href="/" aria-current="page">
            Arguments
          </a>
          <a href="/library" aria-label="Source library">
            Sources
          </a>
        </nav>
        <div className="header-tools">
          <span className="mode-banner">
            <Badge tone={health && !health.kv_available ? "amber" : ""}>
              {!health
                ? "Connecting…"
                : !health.kv_available
                  ? "KV unavailable"
                  : health.provider.live_model
                    ? "Live model"
                    : "Example mode"}
            </Badge>
          </span>
          <details className="connection-details">
            <summary>Connection</summary>
            <div>
              <p>{health?.provider.name ?? "Connecting…"}</p>
              <p>{health?.kv_available ? "KV connected" : "KV unavailable"}</p>
              <p>Version {health?.version ?? "…"}</p>
            </div>
          </details>
        </div>
      </header>
      <div className="app-shell">
        <aside className="sidebar">
          <button
            className="primary wide"
            aria-label="New argument review"
            onClick={newReview}
          >
            + New argument
          </button>
          <details
            className="saved-reviews"
            open={historyOpen}
            onToggle={(event) => setHistoryOpen(event.currentTarget.open)}
          >
            <summary>Saved reviews ({runs.length})</summary>
            <div className="run-list">
              {runs.length ? (
                runs.map((item) => (
                  <button
                    key={item.id}
                    className={run?.id === item.id ? "selected" : ""}
                    onClick={() => {
                      setPage("workspace");
                      setMessage("");
                      watch(item.id);
                    }}
                  >
                    <span>{item.question}</span>
                    <small>{labels[item.status] || item.status}</small>
                  </button>
                ))
              ) : (
                <p className="muted">No saved reviews.</p>
              )}
            </div>
          </details>
        </aside>
        <main id="main">
          {message && (
            <div
              role="alert"
              className={
                message.startsWith("snapshot_verified") ? "notice" : "error"
              }
            >
              <strong>
                {message.startsWith("snapshot_verified")
                  ? "Packet verified"
                  : "Needs attention"}
              </strong>
              <p>{message}</p>
            </div>
          )}
          {page === "new" && request && (
            <>
              <section className="intro">
                <h1>New argument</h1>
              </section>
              <form key={formResetKey} onSubmit={start} className="panel setup">
                <div className="source-tools">
                  <button type="button" onClick={act(selectLibrary)}>
                    Choose from library
                  </button>
                  <details className="example-picker">
                    <summary>Try an example</summary>
                    <div className="example-row">
                      <label>
                        Example
                        <select
                          aria-label="Example or original passage"
                          value={example}
                          onChange={(e) => setExample(e.target.value)}
                        >
                          <option value="">Choose an example…</option>
                          {examples.map((e) => (
                            <option key={e.id} value={e.id}>
                              {e.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button
                        type="button"
                        disabled={!example}
                        onClick={() => {
                          const selected = examples.find(
                            (e) => e.id === example,
                          );
                          if (!selected) return;
                          setRequest(clone(selected.request));
                          setMessage("");
                        }}
                      >
                        Use example
                      </button>
                    </div>
                  </details>
                </div>
                {library.length > 0 && (
                  <div className="example-row">
                    <label>
                      Stored passage
                      <select
                        aria-label="Stored passage"
                        value={libraryChoice}
                        onChange={(e) => setLibraryChoice(e.target.value)}
                      >
                        <option value="">Choose a pinned passage</option>
                        {library.map((s) => (
                          <option key={s.chunk_id} value={s.chunk_id}>
                            {s.title} · {s.version} · {s.text.slice(0, 60)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      type="button"
                      disabled={!libraryChoice}
                      onClick={useLibrary}
                    >
                      Use stored passage
                    </button>
                  </div>
                )}
                <div className="setup-grid">
                  <section
                    className="passage-input"
                    aria-label="Source passage"
                  >
                    <label>
                      Source title
                      <input
                        aria-label="Source title"
                        required
                        maxLength={180}
                        value={source.title}
                        onChange={(e) =>
                          setRequest({
                            ...request,
                            sources: [
                              {
                                ...source,
                                title: e.target.value,
                                binding: null,
                              },
                              ...request.sources.slice(1),
                            ],
                          })
                        }
                      />
                    </label>
                    <label>
                      Selected passage
                      <textarea
                        aria-label="Selected passage"
                        placeholder="Paste your passage here"
                        required
                        rows={11}
                        maxLength={18000}
                        value={source.text}
                        onChange={(e) =>
                          setRequest({
                            ...request,
                            sources: [
                              {
                                ...source,
                                text: e.target.value,
                                kind: "user_text",
                                binding: null,
                              },
                              ...request.sources.slice(1),
                            ],
                          })
                        }
                      />
                    </label>
                    <div className="field-note">
                      <span>{sourceWordCount} / 1,500 words</span>
                      {source.binding && <span>Pinned source</span>}
                    </div>
                  </section>
                  <section
                    className="analysis-input"
                    aria-label="Analysis settings"
                  >
                    <label>
                      Your analysis question
                      <textarea
                        aria-label="Your analysis question"
                        placeholder="Enter your analysis question"
                        required
                        minLength={5}
                        maxLength={1000}
                        rows={4}
                        value={request.question}
                        onChange={(e) =>
                          setRequest({ ...request, question: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      Generation workflow
                      <select
                        aria-label="Generation workflow"
                        value={workflowMode}
                        onChange={(e) => setWorkflowMode(e.target.value)}
                      >
                        <option value="single_draft">Single draft</option>
                        <option value="bounded_review">
                          Draft with critique and revision
                        </option>
                      </select>
                    </label>
                    <details>
                      <summary>Source reference and context</summary>
                      <label>
                        Reference URL
                        <input
                          aria-label="Reference URL"
                          type="url"
                          value={source.reference_url || ""}
                          onChange={(e) =>
                            setRequest({
                              ...request,
                              sources: [
                                {
                                  ...source,
                                  reference_url: e.target.value || null,
                                },
                                ...request.sources.slice(1),
                              ],
                            })
                          }
                        />
                      </label>
                      <label>
                        Notes and interpretation constraints
                        <textarea
                          aria-label="Notes and interpretation constraints"
                          rows={2}
                          maxLength={2000}
                          value={request.notes}
                          onChange={(e) =>
                            setRequest({ ...request, notes: e.target.value })
                          }
                        />
                      </label>
                      {request.sources.slice(1).map((s, i) => (
                        <label key={s.id}>
                          Context passage {i + 1}
                          <textarea
                            maxLength={18000}
                            rows={3}
                            value={s.text}
                            onChange={(e) =>
                              setRequest((p) => ({
                                ...p,
                                sources: p.sources.map((v, n) =>
                                  n === i + 1
                                    ? { ...v, text: e.target.value }
                                    : v,
                                ),
                              }))
                            }
                          />
                        </label>
                      ))}
                      {request.sources.length < 3 && (
                        <button
                          type="button"
                          onClick={() =>
                            setRequest((p) => ({
                              ...p,
                              sources: [
                                ...p.sources,
                                {
                                  id: "context-" + p.sources.length,
                                  title: "Additional context",
                                  version: "v1",
                                  text: "",
                                  kind: "user_text",
                                  reference_url: null,
                                  binding: null,
                                },
                              ],
                            }))
                          }
                        >
                          Add context passage
                        </button>
                      )}
                    </details>
                  </section>
                </div>
                <div className="form-end">
                  {health?.provider.live_model && (
                    <p>The passage will be sent to the configured model.</p>
                  )}
                  <button
                    className="primary"
                    disabled={busy || !health?.kv_available || !canStart}
                  >
                    {busy
                      ? "Starting…"
                      : health?.provider.live_model
                        ? "Start argument review"
                        : "Run demo review"}
                  </button>
                </div>
              </form>
            </>
          )}
          {page === "workspace" && run && (
            <>
              <section className="run-heading">
                <div>
                  <h1>{run.request.sources[0].title}</h1>
                  <p>{run.request.question}</p>
                </div>
                <Badge tone={run.status === "succeeded" ? "green" : "amber"}>
                  {labels[run.status] || run.status}
                </Badge>
              </section>
              <div className="stagebar">
                {[
                  "Source",
                  "Draft",
                  "Checks",
                  "Revision",
                  "Review",
                  "Report",
                ].map((stage, i) => (
                  <span
                    key={stage}
                    className={
                      run.status === "succeeded" ||
                      run.events.some(
                        (e) =>
                          e.node === "node_completed" &&
                          e.stage ===
                            {
                              0: "evidence",
                              1: "draft",
                              2: "audit",
                              3: "critique",
                              5: "report",
                            }[i],
                      ) ||
                      (i === 4 && run.status === "awaiting_review")
                        ? "done"
                        : ""
                    }
                  >
                    {stage}
                  </span>
                ))}
              </div>
              {running && (
                <div className="panel working" role="status">
                  <span className="pulse" />
                  <div>
                    <strong>Preparing a review draft</strong>
                  </div>
                </div>
              )}
              {run.error && (
                <section className="panel">
                  <h2>Run interrupted</h2>
                  <p>{run.error}</p>
                  {["failed", "interrupted"].includes(run.status) && (
                    <button
                      onClick={act(async () => {
                        await api("argument-runs/" + run.id + "/retry", {});
                        watch(run.id);
                      })}
                    >
                      Retry from saved progress
                    </button>
                  )}
                </section>
              )}
              {draft && (
                <>
                  <nav className="workspace-nav">
                    {run.report && (
                      <button onClick={() => setPage("report")}>
                        Open report ↗
                      </button>
                    )}
                    <button
                      disabled={jsonDirty}
                      onClick={() =>
                        download("argument-draft.json", {
                          request: run.request,
                          draft,
                        })
                      }
                    >
                      Export working draft
                    </button>
                    {["running", "queued", "awaiting_review"].includes(
                      run.status,
                    ) && (
                      <button
                        onClick={act(async () => {
                          await api("argument-runs/" + run.id + "/cancel", {});
                          watch(run.id);
                        })}
                      >
                        Stop run
                      </button>
                    )}
                  </nav>
                  <div className="review-grid">
                    <aside className="panel source-panel">
                      <h2>Source</h2>
                      {run.request.sources.map((s) => (
                        <div key={s.id}>
                          <h3>{s.title}</h3>
                          <span className="source-version">{s.version}</span>
                          <blockquote>{s.text}</blockquote>
                          {s.reference_url && (
                            <a
                              href={s.reference_url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              Reference ↗
                            </a>
                          )}
                        </div>
                      ))}
                    </aside>
                    <div className="draft-column">
                      <section className="panel">
                        <div className="panel-title">
                          <div>
                            <h2>Reconstructed argument</h2>
                          </div>
                          {(run.revision > 0 || run.feedback_round > 0) && (
                            <Badge>Revised</Badge>
                          )}
                        </div>
                        {draft.summary !== reading?.summary && (
                          <details className="reading-overview">
                            <summary>Overview</summary>
                            <p>{draft.summary}</p>
                          </details>
                        )}
                        {draft.readings.length > 1 && (
                          <label>
                            Reading
                            <select
                              aria-label="Reading"
                              value={readingIndex}
                              onChange={(e) =>
                                setReadingIndex(Number(e.target.value))
                              }
                            >
                              {draft.readings.map((r, i) => (
                                <option key={r.id} value={i}>
                                  {r.id} · {r.summary}
                                </option>
                              ))}
                            </select>
                          </label>
                        )}
                        <p>{reading?.summary}</p>
                        <ArgumentGraph
                          reading={reading}
                          editable={editable}
                          onChange={(value) => {
                            const next = clone(draft);
                            next.readings[readingIndex] = value;
                            setDraft(next);
                            setJsonText("");
                            setJsonDirty(false);
                            setJsonError("");
                          }}
                        />
                        <fieldset
                          className="formula-scope"
                          disabled={!editable}
                        >
                          <legend>Check scope</legend>
                          <label className="checkbox">
                            <input
                              type="checkbox"
                              aria-label="Check declared formulas only"
                              checked={!!reading?.formal_scope}
                              onChange={(e) => {
                                const next = clone(draft);
                                next.readings[readingIndex].formal_scope = e
                                  .target.checked
                                  ? { mode: "declared_formulas", rationale: "" }
                                  : null;
                                setDraft(next);
                                setJsonText("");
                                setJsonDirty(false);
                                setJsonError("");
                              }}
                            />
                            Check only the declared formulas and dependencies.
                          </label>
                          {reading?.formal_scope && (
                            <label>
                              Explain the checked scope and exclusions
                              <textarea
                                aria-label="Formula scope rationale"
                                minLength={20}
                                maxLength={2000}
                                value={reading.formal_scope.rationale}
                                onChange={(e) => {
                                  const next = clone(draft);
                                  next.readings[
                                    readingIndex
                                  ].formal_scope.rationale = e.target.value;
                                  setDraft(next);
                                  setJsonText("");
                                  setJsonDirty(false);
                                  setJsonError("");
                                }}
                              />
                            </label>
                          )}
                          {reading?.qualifications.some(
                            (q) => q.representation === "unencoded",
                          ) && (
                            <>
                              <p>Not encoded in this check:</p>
                              <ul>
                                {reading.qualifications
                                  .filter(
                                    (q) => q.representation === "unencoded",
                                  )
                                  .map((q, i) => (
                                    <li key={i}>{q.description}</li>
                                  ))}
                              </ul>
                            </>
                          )}
                        </fieldset>
                        <details className="node-details">
                          <summary>
                            Claims and sources ({reading?.nodes.length ?? 0})
                          </summary>
                          {reading?.nodes.map((node, index) => (
                            <article
                              className={
                                "argument-node " +
                                (node.commitment === "proposed_assumption"
                                  ? "assumption"
                                  : "")
                              }
                              key={node.id}
                            >
                              <div className="node-heading">
                                <strong>{node.id}</strong>
                                <span>{node.role}</span>
                                <Badge
                                  tone={
                                    node.commitment === "proposed_assumption"
                                      ? "amber"
                                      : ""
                                  }
                                >
                                  {node.commitment.replaceAll("_", " ")}
                                </Badge>
                              </div>
                              <label
                                className="sr-only"
                                htmlFor={"content-" + node.id}
                              >
                                Content {node.id}
                              </label>
                              <textarea
                                id={"content-" + node.id}
                                aria-label={"Content " + node.id}
                                disabled={!editable}
                                rows={2}
                                value={node.content}
                                onChange={(e) =>
                                  editNode(index, "content", e.target.value)
                                }
                              />
                              <div className="node-fields">
                                <label>
                                  Speaker
                                  <input
                                    aria-label={"Speaker " + node.id}
                                    disabled={!editable}
                                    value={node.speaker}
                                    onChange={(e) =>
                                      editNode(index, "speaker", e.target.value)
                                    }
                                  />
                                </label>
                                <label>
                                  Commitment
                                  <select
                                    aria-label={"Commitment " + node.id}
                                    disabled={!editable}
                                    value={node.commitment}
                                    onChange={(e) =>
                                      editNode(
                                        index,
                                        "commitment",
                                        e.target.value,
                                      )
                                    }
                                  >
                                    {[
                                      "author_position",
                                      "opponent_position",
                                      "hypothetical",
                                      "proposed_assumption",
                                    ].map((v) => (
                                      <option key={v} value={v}>
                                        {v.replaceAll("_", " ")}
                                      </option>
                                    ))}
                                  </select>
                                </label>
                              </div>
                              <label>
                                Grounds / assumption rationale
                                <textarea
                                  aria-label={"Rationale " + node.id}
                                  disabled={!editable}
                                  rows={2}
                                  value={node.rationale}
                                  onChange={(e) =>
                                    editNode(index, "rationale", e.target.value)
                                  }
                                />
                              </label>
                              <p className="uses">
                                Uses:{" "}
                                {node.depends_on.join(", ") ||
                                  "No preceding inference"}
                              </p>
                              {node.source_refs.map((ref, i) => (
                                <blockquote className="node-reference" key={i}>
                                  {ref.quote}
                                  <small>
                                    {ref.source_id} · code points {ref.start}–
                                    {ref.end}
                                  </small>
                                </blockquote>
                              ))}
                              {!node.source_refs.length && (
                                <small className="assumption-note">
                                  Added claim: provide its attribution and
                                  grounds.
                                </small>
                              )}
                            </article>
                          ))}
                        </details>
                        {editable && (
                          <button
                            type="button"
                            onClick={() => {
                              const nodes = reading.nodes;
                              let n = 1;
                              while (nodes.some((node) => node.id === "A" + n))
                                n++;
                              setDraft((previous) => {
                                const next = clone(previous);
                                next.readings[readingIndex].nodes.push({
                                  id: "A" + n,
                                  content: "",
                                  role: "premise",
                                  speaker: "Reader proposal",
                                  commitment: "proposed_assumption",
                                  source_refs: [],
                                  depends_on: [],
                                  rationale: "",
                                });
                                return next;
                              });
                              setJsonText("");
                            }}
                          >
                            ＋ Add an explicit assumption
                          </button>
                        )}
                        <details>
                          <summary>
                            Formula mapping and complete draft JSON
                          </summary>
                          <textarea
                            aria-label="Advanced draft JSON"
                            disabled={!editable}
                            rows={14}
                            value={jsonText || JSON.stringify(draft, null, 2)}
                            onChange={(e) => {
                              setJsonText(e.target.value);
                              setJsonDirty(true);
                              try {
                                JSON.parse(e.target.value);
                                setJsonError("");
                              } catch (error) {
                                setJsonError(error.message);
                              }
                            }}
                          />
                          {editable && (
                            <button
                              type="button"
                              disabled={!jsonDirty || !!jsonError}
                              onClick={act(async () => {
                                const value = await api("argument-validate", {
                                  request: run.request,
                                  draft: JSON.parse(jsonText),
                                });
                                setDraft(value.draft);
                                setReadingIndex(0);
                                setJsonText(
                                  JSON.stringify(value.draft, null, 2),
                                );
                                setJsonDirty(false);
                                setJsonError("");
                              })}
                            >
                              Apply validated JSON changes
                            </button>
                          )}
                          {jsonDirty && (
                            <p className="muted">
                              Apply valid JSON changes before recording a review
                              or exporting.
                            </p>
                          )}
                          {jsonError && <p className="error">{jsonError}</p>}
                        </details>
                      </section>
                      <section className="panel">
                        <h2>Issues and revisions</h2>
                        {JSON.stringify(draft) !==
                          JSON.stringify(run.draft) && (
                          <p role="status">
                            Unsaved edits. Checks will refresh when submitted.
                          </p>
                        )}
                        {run.candidate_checks?.length > 0 &&
                          JSON.stringify(draft) ===
                            JSON.stringify(run.draft) && (
                            <section aria-label="Candidate formal checks">
                              <h3>
                                Candidate formal checks · awaiting your review
                              </h3>
                              {run.candidate_checks.map((result) => (
                                <article key={result.reading_id}>
                                  <Badge tone="amber">
                                    {result.outcome.replaceAll("_", " ")}
                                  </Badge>
                                  <p>{result.reason || result.scope}</p>
                                  <details>
                                    <summary>Formula check evidence</summary>
                                    <pre>{JSON.stringify(result, null, 2)}</pre>
                                  </details>
                                </article>
                              ))}
                            </section>
                          )}
                        {run.issues.length ? (
                          run.issues.map((issue) => (
                            <article className="issue" key={issue.id}>
                              <Badge tone="amber">
                                {issue.kind.replaceAll("_", " ")}
                              </Badge>
                              <p>{issue.message}</p>
                              <small>
                                {issue.reading_id} / {issue.node_id}
                              </small>
                            </article>
                          ))
                        ) : (
                          <p>No open issues.</p>
                        )}
                        {run.issue_history
                          .filter((i) => i.status !== "open")
                          .map((issue) => (
                            <div className="resolved" key={issue.id}>
                              <strong>Revision recorded</strong>
                              <p>{issue.message}</p>
                              <small>{issue.status.replaceAll("_", " ")}</small>
                            </div>
                          ))}
                      </section>
                      {run.revision_feedback && (
                        <section className="panel">
                          <h2>Your revision request</h2>
                          <p>{run.revision_feedback.feedback}</p>
                          <p>Recorded by {run.revision_feedback.reviewer}.</p>
                        </section>
                      )}
                      {editable && run.feedback_available && (
                        <form
                          className="panel feedback-review"
                          onSubmit={requestRevision}
                        >
                          <h2>Ask for one revision</h2>
                          {run.provider.live_model && (
                            <p>
                              Uses one model attempt and includes your current
                              edits.
                            </p>
                          )}
                          <label>
                            Feedback reviewer
                            <input
                              aria-label="Feedback reviewer"
                              required
                              maxLength={180}
                              value={feedbackReviewer}
                              onChange={(e) =>
                                setFeedbackReviewer(e.target.value)
                              }
                            />
                          </label>
                          <label>
                            Feedback for revision
                            <textarea
                              aria-label="Feedback for revision"
                              required
                              minLength={5}
                              maxLength={2000}
                              rows={4}
                              value={feedbackText}
                              onChange={(e) => setFeedbackText(e.target.value)}
                            />
                          </label>
                          <button
                            disabled={busy || jsonDirty || !!jsonError}
                            type="submit"
                          >
                            Revise once with my feedback
                          </button>
                        </form>
                      )}
                      {editable && !run.feedback_available && (
                        <p className="notice">
                          Revision used. You can still edit the draft.
                        </p>
                      )}
                      {editable && (
                        <form className="panel human-review" onSubmit={finish}>
                          <h2>Review the current interpretation</h2>
                          <div className="judgments">
                            {[
                              ["fidelity", "Faithful to the passage"],
                              ["attribution", "Correct attribution"],
                              ["interpretation", "Defensible interpretation"],
                            ].map(([key, label]) => (
                              <label key={key}>
                                {label}
                                <select
                                  required
                                  aria-label={label}
                                  value={review[key]}
                                  onChange={(e) =>
                                    setReview((p) => ({
                                      ...p,
                                      [key]: e.target.value,
                                    }))
                                  }
                                >
                                  <option value="">Record a judgment…</option>
                                  <option value="accepted">Accept</option>
                                  <option value="disputed">Dispute</option>
                                </select>
                              </label>
                            ))}
                          </div>
                          <label>
                            Reviewer label
                            <input
                              required
                              maxLength={180}
                              aria-label="Reviewer label"
                              value={review.reviewer}
                              onChange={(e) =>
                                setReview({
                                  ...review,
                                  reviewer: e.target.value,
                                })
                              }
                            />
                          </label>
                          <label>
                            Reason for these judgments
                            <textarea
                              required
                              minLength={5}
                              rows={3}
                              maxLength={2000}
                              aria-label="Reason for these judgments"
                              value={review.rationale}
                              onChange={(e) =>
                                setReview({
                                  ...review,
                                  rationale: e.target.value,
                                })
                              }
                            />
                          </label>
                          <label className="checkbox">
                            <input
                              type="checkbox"
                              aria-label="Approve the displayed formal mapping"
                              checked={review.approve_formal_mapping}
                              onChange={(e) =>
                                setReview({
                                  ...review,
                                  approve_formal_mapping: e.target.checked,
                                })
                              }
                            />
                            I approve the displayed formal mapping and recorded
                            scope.
                          </label>
                          {draft.readings.some((r) => r.formal_scope) && (
                            <label className="checkbox">
                              <input
                                type="checkbox"
                                aria-label="Confirm formula-only scope"
                                checked={review.approve_formula_scope}
                                onChange={(e) =>
                                  setReview({
                                    ...review,
                                    approve_formula_scope: e.target.checked,
                                  })
                                }
                              />
                              I confirm the formula-only scope; unencoded
                              qualifications remain unresolved.
                            </label>
                          )}
                          <details className="content-identity">
                            <summary>Content identifier</summary>
                            <code className="hash">{run.draft_ref}</code>
                          </details>
                          <button
                            className="primary"
                            disabled={busy || !!jsonError || jsonDirty}
                          >
                            {busy ? "Recording…" : "Record review & finish"}
                          </button>
                        </form>
                      )}
                    </div>
                  </div>
                </>
              )}
              <VersionComparison key={run.id} run={run} />
              <details className="panel trace">
                <summary>Execution history and technical trace</summary>
                {run.events.map((event) => (
                  <div className="trace-row" key={event.id}>
                    <strong>{event.node.replaceAll("_", " ")}</strong>
                    <span>
                      {event.stage || event.operation || event.category || ""}
                    </span>
                    {event.transport && (
                      <Badge tone="green">
                        {event.cache_hit ? "KV reuse" : "KV → tool process"}
                      </Badge>
                    )}
                    <details>
                      <summary>Record</summary>
                      <pre>{JSON.stringify(event, null, 2)}</pre>
                    </details>
                  </div>
                ))}
              </details>
            </>
          )}
          {page === "report" && run?.report && (
            <>
              <section className="run-heading">
                <div>
                  <h1>{run.report.draft.title}</h1>
                  <p>{run.report.request.question}</p>
                </div>
                <Badge
                  tone={
                    run.report.review_status === "accepted" ? "green" : "amber"
                  }
                >
                  {run.report.review_status}
                </Badge>
              </section>
              <div className="workspace-nav">
                <button onClick={() => setPage("workspace")}>
                  ← Review workspace
                </button>
                <button
                  className="primary"
                  onClick={act(() => exportReport("json"))}
                >
                  Export report JSON
                </button>
                <button onClick={act(() => exportReport("html"))}>
                  Export review HTML
                </button>
              </div>
              <section className="panel">
                <h2>Argument analysis</h2>
                <p>{run.report.draft.summary}</p>
                {run.report.request.sources.map((source) => (
                  <details key={source.id}>
                    <summary>
                      Source: {source.title} · {source.version}
                    </summary>
                    <blockquote>{source.text}</blockquote>
                  </details>
                ))}
                {run.report.draft.readings.map((reading) => (
                  <article key={reading.id}>
                    <h3>{reading.id}</h3>
                    <p>{reading.summary}</p>
                    {reading.nodes.map((node) => (
                      <div
                        className={
                          "report-node " +
                          (node.commitment === "proposed_assumption"
                            ? "proposed"
                            : "")
                        }
                        key={node.id}
                      >
                        <div className="node-heading">
                          <strong>{node.id}</strong>
                          <span>{node.role}</span>
                          <Badge
                            tone={
                              node.commitment === "proposed_assumption"
                                ? "amber"
                                : ""
                            }
                          >
                            {node.commitment.replaceAll("_", " ")}
                          </Badge>
                        </div>
                        <p>{node.content}</p>
                        <small>
                          {node.speaker} · Uses:{" "}
                          {node.depends_on.join(", ") ||
                            "no preceding inference"}
                        </small>
                        <p className="muted">{node.rationale}</p>
                        {node.source_refs.map((ref, i) => (
                          <blockquote className="node-reference" key={i}>
                            {ref.quote}
                            <small>
                              {ref.source_id} · code points {ref.start}–
                              {ref.end}
                            </small>
                          </blockquote>
                        ))}
                      </div>
                    ))}
                    <details>
                      <summary>
                        Formal mapping and semantic qualifications
                      </summary>
                      <pre>
                        {JSON.stringify(
                          {
                            formal_mapping: reading.formal_mapping,
                            qualifications: reading.qualifications,
                          },
                          null,
                          2,
                        )}
                      </pre>
                    </details>
                  </article>
                ))}
              </section>
              <section className="panel">
                <h2>Formal checks and review</h2>
                <p>{run.report.scope}</p>
                <p>Reviewer: {run.report.human_review.reviewer}</p>
                <blockquote>{run.report.human_review.rationale}</blockquote>
                <h3>Formal checks</h3>
                {run.report.formal_checks.map((result) => (
                  <article className="result" key={result.reading_id}>
                    <Badge
                      tone={
                        result.outcome === "no_counterexample_complete"
                          ? "green"
                          : "amber"
                      }
                    >
                      {result.outcome.replaceAll("_", " ")}
                    </Badge>
                    <p>{result.reason || result.scope}</p>
                    {result.finite_model && (
                      <>
                        <h4>Independently checked finite countermodel</h4>
                        <pre>
                          {JSON.stringify(result.finite_model, null, 2)}
                        </pre>
                      </>
                    )}
                    {result.assignment && (
                      <pre>{JSON.stringify(result.assignment, null, 2)}</pre>
                    )}
                    <details>
                      <summary>Verification record</summary>
                      {result.evidence?.kind === "trusted-solver-unsat" && (
                        <p>
                          Z3 reports the declared query as unsatisfiable. The
                          recorded proof has not been independently
                          proof-checked.
                        </p>
                      )}
                      <pre>{JSON.stringify(result, null, 2)}</pre>
                    </details>
                  </article>
                ))}
                <h3>Remaining issues</h3>
                {run.report.current_issues.length ? (
                  run.report.current_issues.map((i) => (
                    <p key={i.id}>{i.message}</p>
                  ))
                ) : (
                  <p>No open issues.</p>
                )}
              </section>
            </>
          )}
          {!run && page === "workspace" && (
            <div className="panel">Opening saved review…</div>
          )}
          <footer>
            <details className="packet-verifier">
              <summary>Verify an exported report</summary>
              <label className="verify-file">
                Verify exported packet
                <input
                  aria-label="Verify exported packet"
                  type="file"
                  accept=".json"
                  onChange={async (e) => {
                    try {
                      const file = e.target.files[0];
                      if (!file || file.size > 2000000)
                        throw new Error("Choose a report under 2 MB");
                      const result = await api(
                        "argument-verify",
                        JSON.parse(await file.text()),
                      );
                      setMessage(result.status);
                    } catch (error) {
                      setMessage(error.message);
                    } finally {
                      e.target.value = "";
                    }
                  }}
                />
              </label>
            </details>
          </footer>
        </main>
      </div>
    </>
  );
}
createRoot(document.getElementById("root")).render(<App />);
