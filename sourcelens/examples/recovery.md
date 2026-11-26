# Write-ahead logs and recovery

## Write-ahead ordering

A write-ahead log records a change before the corresponding data pages are allowed to reach durable storage. Recovery replays committed changes from the log after a crash. The ordering rule matters because an in-memory update alone cannot establish durability.

## Acknowledgement and flushing

A service that acknowledges a write before flushing its log may lose acknowledged changes after power failure. Group commit can share a flush across several transactions, trading additional waiting time for fewer storage synchronizations. The durability contract must state what an acknowledgement actually promises.

## Checkpoints

A checkpoint reduces the amount of log that recovery needs to replay. A checkpoint is not a replacement for the write-ahead log: records after the checkpoint can still contain committed changes that have not reached their final data pages. Safe log truncation depends on which changes are already recoverable from durable state.

## Example-library provenance

These notes were authored for the SourceLens example library. They summarize design questions, rather than reproducing a database manual. For a concrete database implementation, inspect its documented recovery and storage contracts.
