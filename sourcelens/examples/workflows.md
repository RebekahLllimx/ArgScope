# Durable workflow design

## Checkpoints and replay

A workflow checkpoint saves execution state so a later process can resume from a completed boundary. If a process fails during a node, the node may run again. Saving graph state does not by itself make an external payment, notification or API write happen exactly once.

## Idempotent effects

An idempotency key identifies a logical request across retries. The service must bind that key to the original request parameters and reject conflicting reuse. A unique database constraint can make request creation atomic even when two clients retry concurrently.

## Atomic checkpoints

When a checkpoint and its progress event share a database transaction, readers cannot observe an event for state that was never committed. A crash before commit leaves the previous checkpoint intact. A crash after commit resumes from the next stage, provided the saved workflow version remains compatible.

## Retry boundaries

Retry transient connection or lock failures with a finite attempt budget. A malformed document is a permanent input error and should not be retried indefinitely. A bounded workflow records the failed stage and exposes a deliberate retry action after the cause is corrected.

## Example-library provenance

These notes were authored for the SourceLens example library. They describe application-level design patterns; they are not a verbatim copy of a workflow framework's documentation.
