# Evaluation

## Scope

The included evaluation is an authored example-corpus sanity check. Three short engineering notes contain 15 passages about recovery, workflows and retrieval. Twelve labeled questions have a declared split: four development and eight test, of which seven test questions are answerable and one is outside the corpus.

Labels identify document keys and headings. They are not generated from retrieval rankings. All methods search the same version snapshot with `k=6`. The evaluation retains the corpus hashes, case hash, runtime versions, rankings and per-case measurements in JSON.

This is a small, inspectable check of the implementation, not a public benchmark or a blinded estimate of generalization. The questions were authored alongside the application. No neural model or external language-model judge is used.

## Metrics

- **Recall@6:** relevant labeled passages retrieved among the first six, divided by all labeled relevant passages.
- **MRR@6:** reciprocal rank of the first labeled relevant passage, or zero if absent.
- **Unanswerable abstention:** proportion of out-of-corpus questions producing no excerpts. There is only one test question of this kind.
- **Quote verification:** emitted excerpts that are exact substrings of their cited parsed chunks. This tests provenance, not semantic support.
- **Retrieval p95:** observed per-query latency on the small local snapshot, excluding the rest of the research workflow. Index construction and cache state affect timing; these measurements are not a throughput benchmark.

## Initial run — 23 November 2026

| Method | Test Recall@6 | Test MRR@6 | Out-of-corpus abstention | Verified excerpts |
| --- | ---: | ---: | ---: | ---: |
| BM25 | 1.000 | 1.000 | 1/1 | 18/18 |
| Character TF-IDF | 1.000 | 1.000 | 1/1 | 17/17 |
| Reciprocal rank fusion | 1.000 | 1.000 | 1/1 | 18/18 |

All methods locate the labeled passages on this set. There is **no demonstrated retrieval gain from fusion**. The different excerpt totals reflect ranking and sentence selection, not a measured quality advantage. A perfect result here mainly shows that the fixtures are easy for the available methods.

Across two short local runs, observed retrieval p95 ranged from 0.98–1.50 ms for BM25, 0.64–1.31 ms for character TF-IDF and 1.24–1.80 ms for fusion. These are mostly warm-cache measurements over 15 passages, not predictions for long PDFs, thousands of versions or concurrent clients. Accuracy and excerpt-count results were unchanged between the runs.

## Reproduce

```bash
python -m pytest -q
python -m sourcelens.evaluate --output outputs/evaluation.json
```

Choose a new output filename for a new run; existing evaluation files are not overwritten. The runner creates a temporary database, imports the included notes and removes only that temporary database when finished. It does not read or modify the user's working library.

## Next evaluation design

Build a separately reviewed test collection with technical synonyms, near-duplicate claims, outdated versions, contradictory sources and no-answer cases. Label supporting passages before running methods. Keep test questions separate from threshold tuning. Evaluate interpretation/claim support independently of citation existence, and measure cold indexing, warm queries, update behavior and concurrent load separately.
