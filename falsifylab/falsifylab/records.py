"""Standard-library trajectory integrity checks and exploratory measurement.

These checks verify stored identities and arithmetic, not the underlying model's
dynamics. Confirmation remains the responsibility of a frozen experiment plan.
"""
import json
import math

from .result_store import MAX_RECORD_BYTES, check_key, fingerprint


def require_fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError("Unexpected trajectory record fields")


def validate_input(value):
    require_fields(
        value,
        (
            "schema",
            "model",
            "environment",
            "dynamics",
            "initial_state_sha256",
            "observation",
        ),
    )
    if (
        value["schema"] != "trajectory-input-v1"
        or value["observation"]
        != "cooperation-and-grid-sha256-state-0-through-steps-v1"
    ):
        raise ValueError("Unsupported trajectory input or observation")
    check_key(value["initial_state_sha256"])
    require_fields(
        value["model"],
        (
            "adapter",
            "source_file",
            "source_sha256",
            "numpy",
            "scipy",
            "adapter_version",
        ),
    )
    check_key(value["model"]["source_sha256"])
    require_fields(
        value["environment"],
        (
            "python",
            "implementation",
            "system",
            "machine",
            "numpy",
            "scipy",
            "grid_dtype",
            "rng",
            "trajectory_adapter",
        ),
    )
    if value["environment"]["trajectory_adapter"] != 1:
        raise ValueError("Unsupported trajectory adapter")
    if any(
        not isinstance(v, str) or not v or len(v) > 100
        for k, v in value["environment"].items()
        if k != "trajectory_adapter"
    ):
        raise ValueError("Invalid trajectory environment")
    dynamics = value["dynamics"]
    require_fields(
        dynamics, ("grid_size", "steps", "b", "p", "seed", "intervention_time")
    )
    for name, low, high in (
        ("grid_size", 2, 256),
        ("steps", 2, 10000),
        ("seed", 0, 2**64 - 1),
    ):
        if type(dynamics[name]) is not int or not low <= dynamics[name] <= high:
            raise ValueError("Invalid trajectory integer setting")
    for name, low, high in (("b", 1, 2), ("p", 0, 10)):
        number = dynamics[name]
        if (
            type(number) not in (int, float)
            or not math.isfinite(number)
            or not low <= number <= high
        ):
            raise ValueError("Invalid trajectory parameter")
    if dynamics["b"] <= 1:
        raise ValueError("Temptation must exceed one")
    time = dynamics["intervention_time"]
    if time is None:
        if dynamics["p"] != 0:
            raise ValueError("A control trajectory must have zero penalty")
    elif type(time) is not int or not 0 <= time < dynamics["steps"]:
        raise ValueError("Invalid trajectory intervention time")


def validate_record(record, key=None, expected_input=None):
    require_fields(record, ("schema", "key", "input", "result", "result_sha256"))
    if record["schema"] != "trajectory-record-v1":
        raise ValueError("Unsupported trajectory record")
    validate_input(record["input"])
    computed = fingerprint(record["input"])
    if record["key"] != computed or (key is not None and computed != check_key(key)):
        raise ValueError("Trajectory key does not match its input")
    if expected_input is not None and record["input"] != expected_input:
        raise ValueError("Stored trajectory belongs to different inputs")
    result = record["result"]
    require_fields(result, ("cooperation", "state_sha256"))
    if fingerprint(result) != record["result_sha256"]:
        raise ValueError("Trajectory content hash mismatch")
    length = record["input"]["dynamics"]["steps"] + 1
    series, states = result["cooperation"], result["state_sha256"]
    if (
        not isinstance(series, list)
        or len(series) != length
        or not isinstance(states, list)
        or len(states) != length
    ):
        raise ValueError("Trajectory length does not match its horizon")
    if any(
        type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1
        for x in series
    ):
        raise ValueError("Invalid cooperation fraction")
    for state in states:
        check_key(state)
    if states[0] != record["input"]["initial_state_sha256"]:
        raise ValueError("Trajectory initial-state hash mismatch")
    return record


def decode_record(data, key, expected_input=None):
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError("Trajectory record exceeds 512 KiB")

    def unique(pairs):
        value = {}
        for name, item in pairs:
            if name in value:
                raise ValueError("Duplicate trajectory JSON key")
            value[name] = item
        return value

    def reject(_):
        raise ValueError("Nonfinite trajectory JSON value")

    return validate_record(
        json.loads(data, object_pairs_hook=unique, parse_constant=reject),
        key,
        expected_input,
    )


def read_record(store, key):
    data = store.get(check_key(key))
    if data is None:
        raise ValueError(
            "Trajectory is missing; rerun its experiment to repopulate the store"
        )
    return decode_record(data, key)


def measure_pair(control, treated, window):
    validate_record(control)
    validate_record(treated)
    first, second = control["input"], treated["input"]
    a, b = first["dynamics"], second["dynamics"]
    if (
        a["intervention_time"] is not None
        or a["p"] != 0
        or b["intervention_time"] is None
    ):
        raise ValueError("Select a control followed by an intervention trajectory")
    for field in ("model", "environment", "observation", "initial_state_sha256"):
        if first[field] != second[field]:
            raise ValueError(
                "Trajectories have incompatible provenance or initial states"
            )
    if any(a[field] != b[field] for field in ("grid_size", "steps", "b", "seed")):
        raise ValueError("Trajectories have different pairing settings")
    time = b["intervention_time"]
    if type(window) is not int or not 1 <= window <= b["steps"] - time:
        raise ValueError("The observation window must lie entirely after intervention")
    for field in ("cooperation", "state_sha256"):
        if control["result"][field][: time + 1] != treated["result"][field][: time + 1]:
            raise ValueError("Stored pair diverges before the intervention transition")
    control_series = control["result"]["cooperation"]
    treated_series = treated["result"]["cooperation"]
    control_mean = sum(control_series[-window:]) / window
    treated_mean = sum(treated_series[-window:]) / window
    at_intervention = control_series[time]
    return {
        "b": b["b"],
        "p": b["p"],
        "seed": b["seed"],
        "control_key": control["key"],
        "treated_key": treated["key"],
        "final_window": window,
        "control_mean": control_mean,
        "treated_mean": treated_mean,
        "effect": treated_mean - control_mean,
        "cooperation_at_intervention": at_intervention,
        "pre_intervention_regime": "all_defectors"
        if at_intervention == 0
        else "all_cooperators"
        if at_intervention == 1
        else "mixed",
        "control_series": control_series,
        "treated_series": treated_series,
    }


def read_measurement(store, control_key, treated_key, window):
    control, treated = read_record(store, control_key), read_record(store, treated_key)
    return {
        "schema": "exploratory-measurement-v1",
        "backend": store.backend,
        "measurement": measure_pair(control, treated, window),
        "model_sha256": control["input"]["model"]["source_sha256"],
        "scope": "Exploratory remeasurement of stored records; no new simulation, source-model replay or held-out confirmation. Selecting a window with these data requires fresh confirmation for a later confirmatory claim.",
    }
