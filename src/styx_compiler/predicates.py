"""Pure structural predicates over libcst nodes used by the splitter."""

import libcst as cst

from styx_compiler.entity_resolver import EntityResolver

# ── statement-shape predicates ────────────────────────────────────────


def is_gather(stmt: cst.CSTNode) -> bool:
    """Match `<target> = gather(...)` or a bare `gather(...)`."""
    if not isinstance(stmt, cst.SimpleStatementLine) or not stmt.body:
        return False
    element = stmt.body[0]
    if not isinstance(element, (cst.Assign, cst.Expr)):
        return False
    val = element.value
    return isinstance(val, cst.Call) and isinstance(val.func, cst.Name) and val.func.value == "gather"


def is_continue(stmt: cst.CSTNode) -> bool:
    return isinstance(stmt, cst.SimpleStatementLine) and bool(stmt.body) and isinstance(stmt.body[0], cst.Continue)


def is_break(stmt: cst.CSTNode) -> bool:
    return isinstance(stmt, cst.SimpleStatementLine) and bool(stmt.body) and isinstance(stmt.body[0], cst.Break)


def ends_with_raise(body: list) -> bool:
    """Last statement is a `raise` — any following code is unreachable."""
    if not body:
        return False
    last = body[-1]
    if isinstance(last, cst.SimpleStatementLine):
        return any(isinstance(el, cst.Raise) for el in last.body)
    return False


def ends_with_terminator(body: list) -> bool:
    """Last statement is a `return` or `raise` — any following code is unreachable."""
    if not body:
        return False
    last = body[-1]
    if isinstance(last, cst.SimpleStatementLine):
        return any(isinstance(el, (cst.Return, cst.Raise)) for el in last.body)
    return False


# ── remote-call detection ──────────────────────────────────


def any_remote_call(resolver: EntityResolver, stmts: list, loop_context=None) -> bool:
    """True if any statement in `stmts` (or any nested if/for/while body) makes
    a remote call, gather, orcontinue/break (if inside a loop)."""
    for stmt in stmts:
        if resolver.is_remote_call(stmt) or is_gather(stmt):
            return True
        if loop_context and (is_continue(stmt) or is_break(stmt)):
            return True
        if isinstance(stmt, cst.If):
            if any_remote_call(resolver, list(stmt.body.body), loop_context):
                return True
            if stmt.orelse is not None and (
                (
                    isinstance(stmt.orelse, cst.Else)
                    and any_remote_call(resolver, list(stmt.orelse.body.body), loop_context)
                )
                or (isinstance(stmt.orelse, cst.If) and any_remote_call(resolver, [stmt.orelse], loop_context))
            ):
                return True
        if isinstance(stmt, cst.For) and for_contains_remote_call(resolver, stmt):
            return True
        if isinstance(stmt, cst.While) and while_contains_remote_call(resolver, stmt):
            return True
    return False


def if_contains_remote_call(resolver: EntityResolver, node: cst.If, loop_context=None) -> bool:
    if any_remote_call(resolver, list(node.body.body), loop_context):
        return True
    if node.orelse is not None:
        if isinstance(node.orelse, cst.Else):
            if any_remote_call(resolver, list(node.orelse.body.body), loop_context):
                return True
        elif isinstance(node.orelse, cst.If) and if_contains_remote_call(resolver, node.orelse, loop_context):
            return True
    return False


def for_contains_remote_call(resolver: EntityResolver, node: cst.For) -> bool:
    return any_remote_call(resolver, list(node.body.body))


def while_contains_remote_call(resolver: EntityResolver, node: cst.While) -> bool:
    return any_remote_call(resolver, list(node.body.body))
