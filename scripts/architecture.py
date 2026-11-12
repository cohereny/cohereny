#!/usr/bin/env python3
"""
scripts/architecture.py

The boundary detector for ADR-030 (CHY-103).

Why this exists at all, and why *before* the refactor:
  ADR-030 cuts the code into five bounded contexts over a shared kernel. A
  boundary that nothing checks is a request, not a boundary -- and this week
  consisted almost entirely of protections that were documented and did not
  work. A detector written *after* the move would only find what was just
  built; written before, it reports every violation at the moment it appears.

Why a ratchet and not a hard gate, for now:
  Three of the four rules already carry debt: five inward imports, one framework
  import, three raises of HTTPException. A check that fails on day one is
  switched off in week one -- the argument from CHY-84, unchanged.

  Three, not the five that ADR-030 and CONVENTIONS.md first recorded. That
  figure came from `grep -c HTTPException` and counted the import statement and
  a comment in tracing.py alongside the three actual raises. It is the same
  mistake the first layering detector made when it counted a docstring, made by
  the same person on the same week, in the document warning against it.

  The elegant part: a ratchet whose floor is zero *is* a hard gate for every
  rule already at zero. `context_cross_imports` starts at 0 because contexts do
  not exist yet, so it is hard-gated from the first commit and stays that way.
  The other three convert themselves the moment they reach zero, without anyone
  having to remember to flip a switch.

The layer model works before and after the move. Today `core/`, `domain/` and
`router/` are the middle; tomorrow they are `contexts/` and `kernel/`. Both are
listed, so this file does not need editing on the day the directories change --
which is exactly the day nobody would remember to edit it.
"""
from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Adapters. May import anything below; nothing below may import them.
TRANSPORT = ("api",)

#: Everything the transport layer sits on. Both the current shape and the
#: target shape of ADR-030 -- see the module docstring.
BELOW = ("core", "domain", "router", "contexts", "kernel", "steps")

#: Web frameworks. Their presence below the transport layer is the measurable
#: form of "the business rules know about HTTP".
FRAMEWORKS = ("fastapi", "starlette")

#: Named in an except clause or raised below transport, this is the same defect
#: as importing fastapi -- a domain rule expressing itself as a status code.
HTTP_SYMBOL = "HTTPException"

#: Zone 3. Used by every context, uses none of them (ADR-030). The rule that
#: keeps it a foundation rather than another layer in the middle -- which is
#: exactly what core/ turned into.
KERNEL = "kernel"

#: What Zone 1 may reach inside a context. Everything else is its inside:
#: store.py holds the SQL, the aggregate holds the rules, and an adapter that
#: reaches past service.py couples itself to both.
CONTEXT_PUBLIC = ("service", "schema", "errors")

#: ADR-048: a compute step is pure. Its package imports the standard
#: library's arithmetic and itself, nothing else -- no network, no database,
#: no clock, no randomness, no other zone. Held here so "pure" is a counted
#: rule from the first commit, not an intention in a docstring.
STEPS = "steps"
STEPS_ALLOWED = frozenset({
    "__future__", "collections", "dataclasses", "decimal", "fractions",
    "math", "numbers", "statistics", "typing", STEPS,
})


@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    line: int
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  [{self.rule}] {self.detail}"


def _sources() -> list[tuple[str, pathlib.Path]]:
    """(top-level package, file) for every module below the transport layer.

    rglob, not glob. The previous detector used glob("*.py") and would have
    walked straight past contexts/repository/store.py -- silently reporting
    zero violations for the very structure it was written to guard.
    """
    out: list[tuple[str, pathlib.Path]] = []
    for pkg in BELOW:
        base = ROOT / pkg
        if not base.is_dir():
            continue
        out += [(pkg, p) for p in sorted(base.rglob("*.py")) if p.name != "__init__.py"]
    return out


def _imported_modules(tree: ast.AST) -> list[tuple[str, int]]:
    """(dotted module name, line) for every import in the tree."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import and cannot cross a top-level
            # package boundary, so it can never violate these rules.
            if node.level == 0 and node.module:
                found.append((node.module, node.lineno))
        elif isinstance(node, ast.Import):
            found += [(a.name, node.lineno) for a in node.names]
    return found


#: contexts/<name>/<file>.py — the shortest path that identifies a context.
_CONTEXT_PATH_DEPTH = 3


def _context_of(path: pathlib.Path) -> str | None:
    """The context a file belongs to, or None if it is not inside one."""
    rel = path.relative_to(ROOT).parts
    if len(rel) < _CONTEXT_PATH_DEPTH or rel[0] != "contexts":
        return None
    return rel[1]


def _import_violations(pkg: str, rel: str, own_context: str | None,
                       tree: ast.AST) -> list[Violation]:
    """The three rules that read imports."""
    found: list[Violation] = []
    for module, line in _imported_modules(tree):
        head = module.split(".")[0]

        if head in TRANSPORT:
            found.append(Violation(
                "inward_only", rel, line,
                f"{pkg}/ imports {module} — dependencies point inward only",
            ))

        if head in FRAMEWORKS:
            found.append(Violation(
                "framework_free", rel, line,
                f"{module} below the transport layer — the rules must not know about HTTP",
            ))

        if pkg == KERNEL and head == "contexts":
            found.append(Violation(
                "kernel_uses_nothing", rel, line,
                f"kernel/ imports {module} — the kernel is used by contexts, "
                f"never the other way round",
            ))

        if pkg == STEPS and head not in STEPS_ALLOWED:
            found.append(Violation(
                "impure_step", rel, line,
                f"steps/ imports {module} -- a compute step uses arithmetic and "
                f"nothing else (ADR-048)",
            ))

        if own_context and head == "contexts":
            other = module.split(".")[1] if "." in module else None
            if other and other != own_context:
                found.append(Violation(
                    "context_isolation", rel, line,
                    f"context '{own_context}' imports context '{other}' — "
                    f"go through its published interface or the kernel",
                ))
    return found


def _transport_reach_violations(rel: str, tree: ast.AST) -> list[Violation]:
    """Zone 1 reaching past a context's published interface.

    `from contexts.pilots.store import ...` in a router means the adapter knows
    the context's SQL layer. The next schema change then breaks the transport
    layer, and the boundary was decoration.
    """
    found: list[Violation] = []
    for module, line in _imported_modules(tree):
        parts = module.split(".")
        if parts[0] != "contexts" or len(parts) < _CONTEXT_PATH_DEPTH:
            continue
        if parts[2] not in CONTEXT_PUBLIC:
            found.append(Violation(
                "context_encapsulation", rel, line,
                f"{module} reaches into context '{parts[1]}' — "
                f"only {', '.join(CONTEXT_PUBLIC)} are published",
            ))
    return found


def _http_violations(rel: str, tree: ast.AST) -> list[Violation]:
    """HTTPException named below the transport layer.

    Checked as a symbol rather than only as an import: it may arrive through a
    re-export or an alias, and the defect worth naming is the status code, not
    the import line. Counts uses, not mentions -- the earlier regex-based figure
    of five included the import statement and a comment in tracing.py, which is
    the same mistake the first layering detector made with a docstring.
    """
    found: list[Violation] = []
    for node in ast.walk(tree):
        name = node.id if isinstance(node, ast.Name) else (
            node.attr if isinstance(node, ast.Attribute) else None
        )
        if name == HTTP_SYMBOL:
            found.append(Violation(
                "http_below_transport", rel, node.lineno,
                "HTTPException below the transport layer — raise a domain error "
                "and map it once, centrally",
            ))
    return found


def violations() -> list[Violation]:
    """Every boundary violation, with file and line.

    Returned rather than counted, because "5 violations" tells nobody what to
    fix. quality_gate.py reduces this to counts for the ratchet; a human gets
    the list.
    """
    found: list[Violation] = []
    for pkg, path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = str(path.relative_to(ROOT))
        found += _import_violations(pkg, rel, _context_of(path), tree)
        found += _http_violations(rel, tree)

    # Zone 1 is checked too, but against a different rule: the transport layer
    # MAY reach downward -- just not past a context's published interface.
    # Until CHY-101 api/ was never read at all, so an adapter importing
    # contexts/x/store.py would have gone unnoticed.
    for pkg in TRANSPORT:
        base = ROOT / pkg
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            found += _transport_reach_violations(str(path.relative_to(ROOT)), tree)
    return found


def counts() -> dict[str, int]:
    """Per-rule totals for the ratchet. Every rule appears, including the ones
    at zero -- a metric that vanishes when it is clean cannot be seen to
    regress."""
    out = dict.fromkeys(
        ("inward_only", "context_isolation", "framework_free", "http_below_transport",
         "kernel_uses_nothing", "context_encapsulation", "impure_step"), 0
    )
    for v in violations():
        out[v.rule] = out.get(v.rule, 0) + 1
    return out


def main() -> int:
    found = violations()
    if not found:
        print("No boundary violations.")
        return 0

    by_rule: dict[str, list[Violation]] = {}
    for v in found:
        by_rule.setdefault(v.rule, []).append(v)

    print(f"\033[31m{len(found)} boundary violations:\033[0m\n")
    for rule, items in sorted(by_rule.items()):
        print(f"  \033[1m{rule}\033[0m ({len(items)})")
        for v in items:
            print(f"    {v.path}:{v.line}")
            print(f"      {v.detail}")
        print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
