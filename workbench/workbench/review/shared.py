"""Real RESP artifact exchange using atomic EX publication, separate from history."""
import json
from workbench.contracts import canonical, digest
from falsifylab.result_store import KVStore, MAX_RECORD_BYTES, StoreError, check_key


def decode(data, key=None):
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError("Shared artifact exceeds 512 KiB")

    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError("Duplicate artifact JSON key")
            result[k] = v
        return result

    def reject(value):
        raise ValueError("Nonfinite artifact value")

    body = json.loads(data, object_pairs_hook=unique, parse_constant=reject)
    if (
        not isinstance(body, dict)
        or set(body) != {"schema", "kind", "inputs", "payload"}
        or body["schema"] != "review-artifact-v1"
    ):
        raise ValueError("Unsupported shared artifact")
    if key is not None and digest(body) != check_key(key):
        raise ValueError("Shared artifact identity mismatch")
    return body


class Shared:
    def __init__(self, port, namespace="review", ttl=3600):
        self.client = KVStore(port, namespace, timeout=1)
        if type(ttl) is not int or not 1 <= ttl <= 86400:
            raise ValueError("Use a TTL in [1,86400]")
        self.ttl = ttl
        self.namespace = namespace

    def key(self, kind, key):
        return f"argscope:review-v1:{self.namespace}:{kind}:{check_key(key)}".encode()

    def publish(self, body):
        data = canonical(body).encode()
        key = digest(body)
        decode(data, key)
        response = self.client.command(
            b"SET",
            self.key("artifact", key),
            data,
            b"EX",
            str(self.ttl).encode(),
            b"NX",
        )
        if response not in (b"OK", None):
            raise StoreError("KV rejected atomic artifact publication")
        saved = self.client.command(b"GET", self.key("artifact", key))
        if saved is None:
            raise StoreError("Artifact expired before publication completed")
        decode(saved, key)
        if saved != data:
            raise ValueError("A content key contains different bytes")
        return key

    def fetch(self, key):
        data = self.client.command(b"GET", self.key("artifact", key))
        if data is None:
            raise StoreError("Shared artifact expired or is missing")
        return decode(data, key)

    def memo(self, key):
        value = self.client.command(b"GET", self.key("memo", key))
        if value is None:
            return None
        try:
            target = check_key(value.decode("ascii"))
            self.fetch(target)
            return target
        except StoreError:
            return None

    def remember(self, key, target):
        check_key(target)
        response = self.client.command(
            b"SET",
            self.key("memo", key),
            target.encode(),
            b"EX",
            str(self.ttl).encode(),
        )
        if response != b"OK":
            raise StoreError("KV did not acknowledge memo publication")

    def ensure(self, store, key):
        body = store.artifact(key)
        self.publish(body)
        return body
