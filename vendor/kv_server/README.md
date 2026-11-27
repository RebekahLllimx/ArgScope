# Pinned KV Server runtime

This directory contains the ten runtime modules from
[KV Server at commit 415f5a6](https://github.com/RebekahLllimx/kv-server/tree/415f5a62b7975b48a26f74833c153f6bb7303582).
[MANIFEST.json](MANIFEST.json) records the full commit and each module's SHA-256.
The snapshot is bundled for offline installation; startup does not download or
follow the upstream branch.

The service provides binary values, TTL, conditional writes, ordered always-mode
group commits and AOF recovery. The upstream
[engineering design](https://github.com/RebekahLllimx/kv-server/blob/415f5a62b7975b48a26f74833c153f6bb7303582/ENGINEERING_DESIGN.md)
defines its commit and failure contracts and documents the standalone tests and
performance comparison. The original code is published with
[rights reserved](https://github.com/RebekahLllimx/kv-server/blob/415f5a62b7975b48a26f74833c153f6bb7303582/LICENSE).

ArgScope's managed launcher uses this directory by default and puts session AOFs
under the selected application data directory, outside this source directory.
The runtime uses Python's standard library. Set `ARGSCOPE_KV_ROOT` to test another
compatible checkout deliberately. Dependency updates require new source hashes
and actual-KV integration tests; the release validator checks complete runtime
membership and byte identity against this manifest.

Offline maintenance is available as `rewrite_aof.py`: preview by default and
`--apply` for a verified replacement with one protected rollback backup. Stop the
owning service first. The runtime and tool share a persistent `.lock` sidecar;
do not unlink it. See the application guide at
[KV AOF maintenance](../../workbench/docs/MVP_GUIDE.md#offline-aof-maintenance).
