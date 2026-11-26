# ArgScope application

This component contains the argument-review API, React interface, workflow,
provider adapters, persistence and portable reports. Start from the
[project README](../README.md) for installation and an offline example.

| Path | Responsibility |
| --- | --- |
| `workbench/review/` | Contracts, workflow, archive, providers, logic adapters and report generation |
| `workbench/review_app.py` | HTTP composition, workers, health and shutdown |
| `review_web/` | Editable browser source |
| `workbench/review_web/` | Built interface served by the application |
| `tests/`, `tools/`, `studies/` | Tests, experiment runners and frozen study settings |
| `workbench/legacy/` | Compatible earlier CLI and prototype, isolated from the current review product |

The sibling ArgScope, FalsifyLab and SourceLens packages remain independently
usable. The current application composes their logic, source and KV interfaces.

[Engineering design](docs/ENGINEERING_DESIGN.md) explains the ownership boundaries.
[Setup and use](docs/MVP_GUIDE.md) covers providers, feedback, saved versions,
restart and data maintenance. [API](docs/API_REFERENCE.md) and
[logic](docs/FIRST_ORDER_GUIDE.md) references define the exact contracts.
[Technical results](docs/FINAL_REPORT.md) distinguish formula controls,
software acceptance and live-model observations.

Runtime data belongs under an explicitly selected, ignored output directory.
The public source distribution contains no saved login, model key, source PDF,
database or session AOF.
