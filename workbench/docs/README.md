# Documentation

Start with the [project README](../../README.md), then choose the detail needed
for your task.

| Document | Purpose |
| --- | --- |
| [Engineering design](ENGINEERING_DESIGN.md) | Requirements, module/state ownership, algorithms, interfaces, alternatives, failure behavior and testing |
| [Technical report](FINAL_REPORT.md) | Quine/Chalmers cases, source-bound experiments, results and interpretation |
| [Setup and use](MVP_GUIDE.md) | Installation, provider configuration, review, feedback, versions, backup, maintenance and release verification |
| [Logic guide](FIRST_ORDER_GUIDE.md) | Formula grammar, formal assumptions, countermodels and outcome meanings |
| [API reference](API_REFERENCE.md) | JSON routes, request/response contracts, lifecycle states and errors |

The [evidence directory](evidence/) contains selected structured experiment
records; [assets](assets/) contains application screenshots. Experiment records
identify their source snapshot and scope. Old measurements are not silently
reassigned to the current source version.

The component packages retain their own focused documentation:
[ArgScope](../../argscope/README.md), [FalsifyLab](../../falsifylab/README.md)
and [SourceLens](../../sourcelens/README.md).
