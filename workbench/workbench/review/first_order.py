"""Bounded classical first-order checks, with independently evaluated finite models.

One nonempty object domain, equality, unary/binary predicates, no constants or
functions. Quantified formulas are closed; no implicit existential import from
names. Z3 UNSAT is trusted solver evidence, not an independently checked proof.
"""
import itertools
import re
from workbench.contracts import digest

SEMANTICS = "classical-first-order-v1"
TOKEN = re.compile(r"\s*(->|[A-Za-z][A-Za-z0-9_]*|[~&|()=.,])")


def parse(text):
    if not isinstance(text, str) or not 0 < len(text) <= 4000:
        raise ValueError("Use a first-order formula of at most 4000 characters")
    tokens, pos = [], 0
    while pos < len(text.rstrip()):
        match = TOKEN.match(text, pos)
        if not match:
            raise ValueError("Unsupported first-order syntax")
        tokens.append(match[1])
        pos = match.end()
    if len(tokens) > 600:
        raise ValueError("First-order token budget exceeded")
    index = 0

    def take(expected=None):
        nonlocal index
        value = tokens[index] if index < len(tokens) else None
        if value is None or (expected is not None and value != expected):
            raise ValueError("Incomplete or malformed first-order formula")
        index += 1
        return value

    def peek():
        return tokens[index] if index < len(tokens) else None

    def expression(bound, depth=0, minimum=0):
        if depth > 40:
            raise ValueError("First-order nesting budget exceeded")
        token = take()
        if token in ("forall", "exists"):
            variable = take()
            if not re.fullmatch(r"[a-z][a-z0-9_]*", variable):
                raise ValueError("Use lowercase bound variable names")
            take(".")
            left = (token, variable, expression(bound | {variable}, depth + 1))
        elif token == "~":
            left = ("not", expression(bound, depth + 1, 4))
        elif token == "(":
            left = expression(bound, depth + 1)
            take(")")
        elif re.fullmatch(r"[A-Z][A-Za-z0-9_]*", token):
            take("(")
            args = [take()]
            if peek() == ",":
                take(",")
                args.append(take())
            take(")")
            if any(arg not in bound for arg in args):
                raise ValueError(
                    "Free terms/constants are not supported; bind variables explicitly"
                )
            left = ("pred", token, tuple(args))
        elif token in bound:
            take("=")
            other = take()
            if other not in bound:
                raise ValueError("Equality requires two bound variables")
            left = ("eq", token, other)
        else:
            raise ValueError(
                "Expected quantifier, predicate, or bound-variable equality"
            )
        priorities = {"->": 1, "|": 2, "&": 3}
        while peek() in priorities and priorities[peek()] >= minimum:
            op = take()
            precedence = priorities[op]
            right = expression(bound, depth + 1, precedence + (op != "->"))
            left = (op, left, right)
        return left

    result = expression(set())
    if index != len(tokens):
        raise ValueError("Unexpected first-order trailing tokens")
    signatures([result])
    return result


def signatures(asts):
    result = {}

    def visit(ast):
        if ast[0] == "pred":
            name, arity = ast[1], len(ast[2])
            if name in result and result[name] != arity:
                raise ValueError("A predicate must have one fixed arity")
            result[name] = arity
        elif ast[0] in ("forall", "exists"):
            visit(ast[2])
        elif ast[0] == "not":
            visit(ast[1])
        elif ast[0] != "eq":
            visit(ast[1])
            visit(ast[2])

    for ast in asts:
        visit(ast)
    if len(result) > 12:
        raise ValueError("Use at most twelve predicates")
    return result


def evaluate(ast, model, env=None):
    """Evaluate the original AST, independently of Z3's formula translation."""
    env = {} if env is None else env
    op = ast[0]
    if op == "pred":
        key = ",".join(str(env[arg]) for arg in ast[2])
        return model["predicates"][ast[1]][key]
    if op == "eq":
        return env[ast[1]] == env[ast[2]]
    if op == "not":
        return not evaluate(ast[1], model, env)
    if op in ("forall", "exists"):
        values = (
            evaluate(ast[2], model, {**env, ast[1]: i})
            for i in range(model["domain_size"])
        )
        return all(values) if op == "forall" else any(values)
    left, right = evaluate(ast[1], model, env), evaluate(ast[2], model, env)
    return {"&": left and right, "|": left or right, "->": not left or right}[op]


def validate_model(premises, conclusion, model):
    asts = [parse(f) for f in [*premises, conclusion]]
    sig = signatures(asts)
    if (
        set(model) != {"domain_size", "predicates"}
        or type(model["domain_size"]) is not int
        or not 1 <= model["domain_size"] <= 8
    ):
        raise ValueError("Use a complete finite model of one to eight objects")
    if set(model["predicates"]) != set(sig):
        raise ValueError("Model predicates differ from the original formulas")
    for name, arity in sig.items():
        keys = {
            ",".join(map(str, row))
            for row in itertools.product(range(model["domain_size"]), repeat=arity)
        }
        if set(model["predicates"][name]) != keys or any(
            type(v) is not bool for v in model["predicates"][name].values()
        ):
            raise ValueError("Finite predicate tables must be complete Boolean tables")
    values = [evaluate(a, model) for a in asts]
    if not all(values[:-1]) or values[-1]:
        raise ValueError("Model does not falsify the original inference")
    return {
        "status": "formal_witness_verified",
        "validator": "first-order-original-ast-v1",
        "premise_values": values[:-1],
        "conclusion_value": values[-1],
    }


def check(premises, conclusion, *, timeout_ms=1200, rlimit=100000):
    if not isinstance(premises, list) or len(premises) > 20:
        raise ValueError("Use at most twenty first-order premises")
    if (
        type(timeout_ms) is not int
        or not 1 <= timeout_ms <= 1200
        or type(rlimit) is not int
        or not 1 <= rlimit <= 100000
    ):
        raise ValueError("Invalid first-order resource budget")
    asts = [parse(f) for f in [*premises, conclusion]]
    sig = signatures(asts)
    import z3

    ctx = z3.Context(proof=True)
    domain = z3.DeclareSort("Object", ctx=ctx)
    predicates = {
        name: z3.Function(name, *([domain] * arity), z3.BoolSort(ctx))
        for name, arity in sig.items()
    }

    def translate(ast, env):
        op = ast[0]
        if op == "pred":
            return predicates[ast[1]](*[env[arg] for arg in ast[2]])
        if op == "eq":
            return env[ast[1]] == env[ast[2]]
        if op in ("forall", "exists"):
            variable = z3.FreshConst(domain, ast[1])
            body = translate(ast[2], {**env, ast[1]: variable})
            return (z3.ForAll if op == "forall" else z3.Exists)([variable], body)
        if op == "not":
            return z3.Not(translate(ast[1], env))
        left, right = translate(ast[1], env), translate(ast[2], env)
        return {"&": z3.And, "|": z3.Or, "->": z3.Implies}[op](left, right)

    formulas = [translate(ast, {}) for ast in asts]
    solver = z3.Solver(ctx=ctx)
    solver.set(timeout=timeout_ms, rlimit=rlimit, random_seed=0)
    solver.add(*formulas[:-1])
    consistency = solver.check()
    result = {
        "semantics": SEMANTICS,
        "backend": "z3:" + z3.get_version_string(),
        "domain": "nonempty; no constants or functions",
        "input_digest": digest([premises, conclusion]),
        "budget": {"timeout_ms_per_query": timeout_ms, "rlimit_per_query": rlimit},
    }
    if consistency == z3.unsat:
        return {
            **result,
            "outcome": "inconsistent_assumptions",
            "reason": "Premises are inconsistent; no substantive validity claim",
        }
    if consistency == z3.unknown:
        return {
            **result,
            "outcome": "budget_exhausted",
            "reason": solver.reason_unknown(),
        }
    solver.add(z3.Not(formulas[-1]))
    verdict = solver.check()
    if verdict == z3.unknown:
        return {
            **result,
            "outcome": "budget_exhausted",
            "reason": solver.reason_unknown(),
        }
    if verdict == z3.unsat:
        proof = solver.proof().sexpr()
        # The digest identifies complete solver evidence; bounded text is a preview.
        return {
            **result,
            "outcome": "no_counterexample_complete",
            "evidence": {
                "kind": "trusted-solver-unsat",
                "query": "premises AND NOT conclusion",
                "proof_digest": digest(proof),
                "proof_preview": proof[:16000],
                "proof_truncated": len(proof) > 16000,
                "independently_proof_checked": False,
            },
        }
    model = solver.model()
    universe = model.get_universe(domain)
    if universe is None:
        # Force an irrelevant domain witness only for model materialization.
        witness = z3.FreshConst(domain, "domain_witness")
        model.eval(witness, model_completion=True)
        universe = model.get_universe(domain)
    if universe is not None:
        # Z3 may materialize many interchangeable objects. Try small subdomains,
        # but NEVER assume a restriction preserves quantified formulas: accept
        # it only after independently evaluating every original formula.
        for size in range(1, min(len(universe), 8) + 1):
            finite = {"domain_size": size, "predicates": {}}
            for name, arity in sig.items():
                finite["predicates"][name] = {
                    ",".join(map(str, row)): z3.is_true(
                        model.eval(
                            predicates[name](*[universe[i] for i in row]),
                            model_completion=True,
                        )
                    )
                    for row in itertools.product(range(size), repeat=arity)
                }
            try:
                validation = validate_model(premises, conclusion, finite)
            except ValueError:
                continue
            return {
                **result,
                "outcome": "witness_found",
                "finite_model": finite,
                "validation": validation,
            }
    return {
        **result,
        "outcome": "budget_exhausted",
        "reason": "SAT but no independently validated finite witness within eight objects",
    }


def analyze(reading):
    """Check each dependency before allowing its result to support a later step."""
    nodes = {n["id"]: n for n in reading["nodes"]}
    formulas = reading["formal_mapping"]["formulas"]
    ordered = []

    def visit(key):
        if key in ordered:
            return
        for dep in nodes[key]["depends_on"]:
            visit(dep)
        ordered.append(key)

    visit(reading["target"])
    steps, results = [], {}
    statuses = {
        "no_counterexample_complete": "valid",
        "witness_found": "invalid",
        "inconsistent_assumptions": "inconsistent_premises",
        "budget_exhausted": "unknown",
    }
    for key in ordered:
        node = nodes[key]
        if node["role"] == "premise":
            continue
        blocked = [
            dep
            for dep in node["depends_on"]
            if dep in results and results[dep]["status"] != "valid"
        ]
        item = {"id": key, "formula": formulas[key], "depends_on": node["depends_on"]}
        if blocked:
            item.update(status="blocked", blocked_by=blocked)
        else:
            result = check([formulas[dep] for dep in node["depends_on"]], formulas[key])
            item.update(status=statuses[result["outcome"]], result=result)
        results[key] = item
        steps.append(item)
    return {
        "semantics": SEMANTICS,
        "steps": steps,
        "topological_order": ordered,
        "all_steps_valid": all(s["status"] == "valid" for s in steps),
    }
