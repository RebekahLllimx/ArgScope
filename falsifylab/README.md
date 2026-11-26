# FalsifyLab

This component retains independent numerical experiments and supplies original-AST propositional witness validation to the [research workbench](../README.md). The paper-review workflow does not require running the numerical model. Commands below run from this component directory.

Search a computational model for parameter settings where an explicit effect-floor hypothesis may fail, then check the selected settings using fresh initial-state seeds.

The first adapter runs the existing [Leviathan cooperation model](https://github.com/RebekahLllimx/ai-computational-modeling-lab/tree/main/final-project) without changing its dynamics. A candidate is selected on search seeds; confirmation uses disjoint seeds and an explicit uncertainty bound.

## Run

Use Python 3.10+ with NumPy and SciPy. Install this package with `python -m pip install -e .` or use an existing environment containing those dependencies.

```bash
python -m unittest discover -s tests -v

python -m falsifylab \
  --model-root /path/to/ai-computational-modeling-lab \
  --config configs/smoke.json \
  --output outputs/reference \
  --workers 2

# Reuse verified simulation artifacts after an interruption or completed run.
python -m falsifylab \
  --model-root /path/to/ai-computational-modeling-lab \
  --config configs/smoke.json \
  --output outputs/reference \
  --resume
```

The model path must point to a trusted local checkout: loading the adapter executes its Python source. The runner reads the model and writes only to its selected output directory. Use a new output directory for a different model or experiment configuration.

## Experiment workflow

1. Declare an effect threshold, parameter domain, simulation horizon and final evaluation window.
2. Evaluate the same candidate-slot budget with a grid and seeded random search.
3. Run control and intervention from identical initial states, checking that they match before intervention.
4. Select the lowest observed search effect for each method.
5. Evaluate selected settings on fresh confirmation seeds.
6. Report raw effects, uncertainty, initial-state regimes and all inputs needed to replay the experiment.

Effects are differences in final-window cooperation fractions, not percentages. `minimum_effect: 0.05` means a five-percentage-point effect floor. This is a hypothesis chosen for the experiment, not a claim attributed to the original paper.

## Interpretation

The confirmation decision uses a one-sided Hoeffding bound for effects in `[-1,1]`, with Bonferroni adjustment across the declared methods. It assumes independently sampled initial states. With eight confirmation seeds the bound is wide: eight zero effects do not establish a zero expected effect. A Student-t bound is retained as a diagnostic but never decides confirmation.

The small `smoke.json` run validates the execution and reporting path; it is not a replacement for the original 100-by-100 simulation study. Current results concern the synchronous periodic Moore-neighborhood model. Different networks, asynchronous dynamics and adaptive search are future adapters or methods.

## Outputs

| Path | Contents |
| --- | --- |
| `experiment.json` | Fixed configuration, source fingerprint, numerical package versions and experiment version |
| `pairs/*.json` | Content-addressed paired trajectories, inputs, final-window effects and pre-intervention regimes |
| `report.json` | Candidate search results, independent confirmation, uncertainty and cache accounting |
| `report.md` | Readable summary of the complete experiment |

Cached summaries are recomputed from trajectories before reuse. Corrupt artifacts, changed model code, overlapping seed sets and incompatible resume requests stop the run rather than being silently accepted. Threads can run independent initial states concurrently; speedup is workload-dependent.

[Engineering design](docs/ENGINEERING_DESIGN.md)

## Shared trajectories and the existing KV

An optional experiment-v3 path separates dynamics from final-window measurement.
Controls can be reused across penalties; a reader can change a window without
rerunning the model. Choose one disposable store; the legacy pair-cache path
above remains the default and its existing outputs are unchanged.

```bash
python -m falsifylab --model-root /path/to/model --config configs/smoke.json \
  --output outputs/shared --trajectory-store kv --kv-port 16379 --namespace research

python -m falsifylab.reader --store kv --kv-port 16379 --namespace research \
  --control CONTROL_SHA256 --treated TREATED_SHA256 --window 5
```

The reader needs only Python's standard library. Keys are in the experiment-v3
report's candidate `trajectory_pairs` and confirmation `evidence_pairs`.
`--trajectory-store file|sqlite --store-path PATH` selects local alternatives;
the reader uses `--store file|sqlite --store-path PATH`. The workbench's independent
prototype can import the same report and plot/recompute a selected window.

Model/environment/initial-state/dynamics identities are included in each key;
the final window and effect threshold are not. Hits validate complete records
and pre-intervention grid matching. Store errors cause recorded uncached
computation; corrupt records are rejected. No second cache is silently written.
Selected confirmation pairs remain archived in the final report.

The KV adapter uses actual GET/SET/DEL over loopback TCP and one complete record
per value, capped at 512 KiB. The research session has no automatic TTL, eviction,
distributed lock or global memory bound. KV is optional and did not outperform
file/SQLite in the small local measurements. The commands above select the store
and independent reader explicitly. The [component design](docs/ENGINEERING_DESIGN.md)
defines the experiment and cache contracts; the [application design](../workbench/docs/ENGINEERING_DESIGN.md)
describes the separate TTL-based review-tool lifecycle.
