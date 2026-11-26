"""Structural, ID-aligned draft differences; no model judgment of improvements."""
def compare(before, after, path=()):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            if key not in before or key not in after:
                changes.append({"path": list(path + (key,)), "kind": "added" if key not in before else "removed",
                                "before": before.get(key), "after": after.get(key)})
            else:
                changes.extend(compare(before[key], after[key], path + (key,)))
        return changes
    if isinstance(before, list) and isinstance(after, list) and path and path[-1] in ("readings", "nodes"):
        left, right = {v["id"]: v for v in before}, {v["id"]: v for v in after}
        changes = compare(left, right, path)
        if list(left) != list(right):
            changes.append({"path": list(path + ("$order",)), "kind": "order", "before": list(left), "after": list(right)})
        return changes
    return [{"path": list(path), "kind": "changed", "before": before, "after": after}]
