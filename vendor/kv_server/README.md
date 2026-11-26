# Pinned custom KV runtime

This pinned RESP/TCP runtime provides binary-safe values, TTL, conditional writes,
AOF recovery and manual offline log rewrite. `MANIFEST.json` binds its source
files to revision `offline-aof-rewrite-v1-english`. The English-comment update
preserves the executable AST of the independent implementation.

ArgScope’s managed launcher uses this directory by default and puts session AOFs
under workbench/outputs, outside this source directory. The runtime uses Python’s
standard library. Set ARGSCOPE_KV_ROOT to deliberately test another checkout.
Treat updates as explicit dependency changes: compare source hashes, rerun actual
KV integration tests and record a new manifest. Do not silently synchronize it.

Offline maintenance is available as `rewrite_aof.py`: preview by default and
`--apply` for a verified replacement with one protected rollback backup. Stop the
owning service first. The runtime and tool share a persistent `.lock` sidecar;
do not unlink it. See the application guide at
[KV AOF maintenance](../../workbench/docs/MVP_GUIDE.md#offline-aof-maintenance).
