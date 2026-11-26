# FalsifyLab experiment and witness design

FalsifyLab contains a paired numerical experiment runner and a formal-witness adapter. The numerical path searches declared intervention settings and uses disjoint confirmation seeds; optional shared trajectories support independent remeasurement through file, SQLite or RESP/KV storage. The [review application](../../workbench/docs/ENGINEERING_DESIGN.md) also reuses its propositional assignment validator. These are distinct mechanisms with different evidence: a logical countermodel and a stochastic effect estimate are not interchangeable. Open-domain conceptual generation and witness reduction remain unimplemented.

## 1. Problem and requirements

A parameter sweep does not distinguish an outcome selected because it looked unusual from one that persists on new initial states. FalsifyLab makes this distinction explicit: declare an effect-floor hypothesis, search a bounded domain for challenging settings, then evaluate selected settings on disjoint confirmation seeds.

The user is a researcher running computational experiments. The output is an inspectable experiment directory, not a policy recommendation or a replacement for the source model's paper.

| Requirement | Implementation contract |
| --- | --- |
| Declare the claim | Fix an effect floor, parameter domain and measurement window |
| Search fairly | Allocate equal candidate-slot budgets to grid and random search |
| Compare matched conditions | Run control and intervention from identical initial grids |
| Separate selection and confirmation | Reject overlapping seed sets |
| Resume computation | Reuse complete, validated, content-addressed pair artifacts |
| Preserve provenance | Record source hash, numerical package versions and adapter version |
| Report uncertainty | Retain raw effects and a declared one-sided bound |

Non-functional requirements are bounded compute, deterministic scheduling, atomic writes, explicit failures and no writes into the source checkout. This is a local CLI without credentials, a network service or external notifications.

## 2. System architecture

```text
configuration + trusted model checkout
                  │
          validate experiment identity
                  │
        grid / seeded random candidates
                  │
     PairCache ───┼── verified existing pair
                  └── model adapter → control + intervention
                                      │
                           validate and atomically persist
                                      │
                     select candidate on search seeds
                                      │
                     evaluate fresh confirmation seeds
                                      │
                       uncertainty + JSON/Markdown report
```

| Module | Responsibility | Does not own |
| --- | --- | --- |
| `model.py` | Load and fingerprint source, pair initial states, collect trajectories | Selection, statistics or output directories |
| `experiment.py` | Validate configuration, generate candidates, schedule runs, verify cache, select and confirm | The model's update rule |
| `__main__.py` | Parse paths and options; report failures | Scientific assumptions or alternate model semantics |

The adapter imports `final-project/src/ca_model.py` from the selected checkout. Importing Python executes source code, so the checkout must be trusted. The source is neither copied nor edited, and import-time bytecode writes into it are suppressed.

## 3. Interfaces and data design

### Configuration

`Config.from_dict` rejects unknown fields and validates types before execution.

| Field | Meaning / bound |
| --- | --- |
| `grid_size`, `steps` | Grid side in `[2,256]`; transition horizon in `[2,10000]` |
| `intervention_time` | State before the first affected transition; less than `steps` |
| `final_window` | Terminal measurement states, entirely after intervention |
| `temptation` | Lower endpoint greater than 1; upper endpoint at most 2 |
| `penalty` | Positive range, at most 10; control uses zero |
| `budget` | Candidate slots per method, from 1 to 1000 |
| `discovery_seeds`, `confirmation_seeds` | Each 2–100 distinct nonnegative integers; sets disjoint |
| `search_seed` | Nonnegative seed for random candidate generation |
| `minimum_effect` | Expected-effect floor in `[-1,1]`, in population fractions |
| `family_alpha` | Family error level between 0 and 0.5 |
| `methods` | Nonempty subset of `grid` and `random` |

The budget guard counts both trajectories per pair and rejects more than 200 million planned cell transitions. This is not a runtime promise. Cache reuse can reduce actual computation below the estimate.

### Model interface

`ModelSpec.from_root(path)` locates and hashes source. `verify()` checks its identity. `run_pair(spec, settings, b, p, seed)` returns parameters, seed, control/treated cooperation trajectories, final-window means and `treated_mean - control_mean`.

Trajectories contain `steps + 1` states from `t=0` through `t=steps`. Conditions start with identical grids and remain equal through the state at `intervention_time`. Punishment affects the next transition; shifting this boundary changes the experiment.

The adapter also records cooperation at intervention and its regime: `mixed`, `all_cooperators` or `all_defectors`.

### Artifacts

| Artifact | Contents and role |
| --- | --- |
| `experiment.json` | Configuration, source fingerprint, runtime provenance and experiment version |
| `pairs/<sha256>.json` | One complete input/result pair; independently reusable |
| `report.json` | Candidates, selections, confirmation effects, bounds and cache accounting |
| `report.md` | Readable derived view of the JSON result |

Pair keys hash canonical inputs: source hash, NumPy/SciPy versions, adapter version, simulation settings, parameters and seed. Changing dependencies or adapter semantics invalidates reuse.

Before reuse, the cache checks input equality, parameter identity, trajectory length, finite values in `[0,1]`, shared pre-intervention histories, intervention regime and agreement between saved summaries and recalculated means. These checks detect malformed or inconsistent artifacts; they do not prove that an adversarially rewritten trajectory came from the model.

## 4. Core mechanisms and technical decisions

### Paired comparisons

The effect for seed `s` is final-window treated cooperation minus matched control cooperation. Pairing removes differences caused only by using another initial grid. Variation across initial states remains, so seed-level effects stay in the report.

The adapter reads `grid.mean()` after transitions rather than invoking more expensive cluster metrics when cooperation alone is required. Update and tie-breaking rules remain unchanged.

### Candidate search

Grid search uses an evenly spaced lattice. Non-square budgets use deterministic evenly spaced lattice positions; one slot uses the midpoint. Random search uses a seeded NumPy generator and continuous uniform samples in the declared ranges.

Methods receive equal candidate slots, not necessarily distinct points when an interval collapses. Each selects its lowest mean discovery effect, breaking ties by `b`, then `p`. Confirmation outcomes never participate in selection.

### Confirmation and uncertainty

For paired effect `D ∈ [-1,1]`, the one-sided Hoeffding upper bound is:

```text
U = min(1, mean(D) + sqrt(2 * log(1 / alpha) / n))
alpha = family_alpha / number_of_declared_methods
```

If `U < minimum_effect`, the outcome is `held_out_effect_below_floor`; otherwise it is `not_confirmed_below_floor`. The latter does not confirm the original hypothesis: it means this experiment has not established an effect below the floor.

The calculation assumes independently sampled initial states. Bonferroni adjustment covers the declared family without requiring independent search methods. Eight identical observed effects do not make uncertainty vanish. A Student-t upper bound is retained as a diagnostic only.

Absorbing states are reported separately. Zero effect after cooperation has disappeared answers a different question from ineffective intervention in a mixed population. Meaningful preconditions must be defined before interpreting either as a substantive challenge.

### Concurrency and persistence

One to four threads can run independent seeds. Each simulation owns its state and RNG. Imports are guarded; the parent validates and writes in deterministic input order. Threads do not guarantee speedup.

JSON writes use temporary files, flush, `fsync` and atomic replacement. Failed writes preserve earlier complete artifacts; resume recomputes missing work. Atomic replacement protects file visibility, not every device's power-loss behavior; the directory is not explicitly fsynced.

There is no cross-process output lock. Use one writer per experiment directory. Supported concurrency is the internal thread pool, not two CLI processes sharing a directory.

## 5. Testing and validation

Run from the project root:

```bash
python -m unittest discover -s tests -v
python -m falsifylab --model-root /path/to/ai-computational-modeling-lab \
  --config configs/smoke.json --output outputs/reference --workers 2
python -m falsifylab --model-root /path/to/ai-computational-modeling-lab \
  --config configs/smoke.json --output outputs/reference --resume
```

| Validation | Evidence |
| --- | --- |
| Configuration | Types, ranges, finite values, seed separation and guardrails |
| Candidates | Budget, determinism, bounds and midpoint behavior |
| Statistics | Raw effects, conservative bounds and an example crossing a declared floor |
| Pipeline | Selection/confirmation separation, incompatible resume rejection, serial/thread equality |
| Integrity | Changed source, corrupt summaries and failed atomic writes |
| Actual adapter | Separate CLI smoke runs against external HobbesCA source |

The suite has 12 test methods. Pipeline unit tests substitute a deterministic model to isolate orchestration; actual-model runs provide separate integration evidence.

On 23 November 2026, the smoke configuration completed 52 paired simulations, or 104 trajectories: 16×16 cells, 60 transitions, six candidate slots per method, three discovery seeds and eight confirmation seeds. A separate single-thread run matched two-thread results. Resume reused 52 pairs and recomputed none. Source identity remained unchanged.

Both selected settings had mean zero but an upper bound near 0.9603. Both outcomes were `not_confirmed_below_floor`. These execution-path results are not new substantive findings or a replacement for the original 100×100 study.

## 6. Deployment and operations

Use Python 3.10+ with NumPy and SciPy. The CLI accepts a trusted checkout, configuration, output directory, worker count and `--resume`. Malformed input and incompatible artifacts produce stderr explanations and exit code 2.

Existing directories require `--resume` and exact manifest agreement. Nonempty directories without manifests are rejected. Different source, package versions or configuration need a new directory. JSON is authoritative; Markdown is a derived presentation that can be regenerated after interruption.

Operational inspection uses manifests, pair counts, cache accounting and seed-level results. Preserve the source and dependency versions for reproduction on another machine.

## 7. Limitations and extension boundaries

The implemented domain is one synchronous, periodic, Moore-neighborhood model. Other networks, asynchronous updates, interventions or metrics need separate adapters and pairing tests. A source hash does not fingerprint the operating system or every transitive dependency.

Search is grid/random, not adaptive optimization. A substantive study should predeclare hypotheses and regime conditions, justify seed count, repeat search seeds when comparing efficiency, and retain null results. These are experiment-design choices, not conclusions inferred from the most striking run.
