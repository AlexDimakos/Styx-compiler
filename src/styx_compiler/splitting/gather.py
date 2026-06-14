"""Splitting at `gather(...)` calls — fan-out N parallel dispatches into a join step.

Two types of gather supported:
    - static: `(a, b) = gather(x.foo(), y.bar())` — we can determine the number of parallel calls at compile time, so we
    send N calls tagged by static indices and a barrier sized to N.

    - dynamic: `gather(*[e.bar() for e in xs])` — comprehensions with a single for components and currently
    NO FILTERS ARE SUPPORTED!!. Number of parallel calls is determined at runtimes.

Both use the sa,e join continuation that update_gather_barrier helpers.
"""

import libcst as cst

from styx_compiler.cst_helpers import (
    CTX_KEY,
    assign_stmt,
    call_remote_async_stmt,
    context_dict,
    dict_from_pairs,
    init_gather_barrier_stmt,
    params_with_reply_to,
    reply_entry,
    restore_tuple_from,
)
from styx_compiler.splitting.context import LoopContext, SplitContext


def handle_gather(ctx: SplitContext, body: list, i: int, loop_context: LoopContext | None = None) -> list:
    """Split at a `gather(...)` call: fan-out + barrier join."""
    stmt = body[i]
    post_split = body[i + 1 :]

    target_name, tuple_target, gather_call = _extract_gather_target(stmt)

    # Dynamic case: `gather(*<comp>)`
    spread_comp: cst.ListComp | cst.SetComp | cst.GeneratorExp | None = None
    if (
        len(gather_call.args) == 1
        and gather_call.args[0].star == "*"
        and isinstance(gather_call.args[0].value, (cst.ListComp, cst.SetComp, cst.GeneratorExp))
    ):
        spread_comp = gather_call.args[0].value

    if spread_comp is None:
        gather_args = [arg.value for arg in gather_call.args]
        if not gather_args:
            msg = "gather() requires at least one call argument"
            raise ValueError(msg)

    # Snapshot of locals to save across the gather. Take a COPY so that later
    # mutations to ctx.defined_vars (adding gather targets) don't leak into
    # the dispatch block / restore block.
    vars_to_save = set(ctx.liveness.vars_to_save_at(stmt, ctx.defined_vars))

    join_name = ctx.next_step_name()

    # Build dispatch before adding gather targets to defined_vars.
    if spread_comp is not None:
        dispatch_block = _build_spread_dispatch(ctx, spread_comp, join_name, vars_to_save)
    else:
        dispatch_block = _build_static_dispatch(ctx, gather_args, join_name, vars_to_save)

    # The gather's targets become defined AFTER the join step resumes —
    # add them now so post-gather processing (inside join body) tracks them.
    if target_name is not None:
        ctx.defined_vars.add(target_name)
    if tuple_target is not None:
        for el in tuple_target.elements:
            if isinstance(el.value, cst.Name):
                ctx.defined_vars.add(el.value.value)

    join_body = _build_join_body(ctx, target_name, tuple_target, vars_to_save, post_split, loop_context)
    join_func = ctx.make_continuation(join_name, join_body, "_gather_partial")
    ctx.generated_functions.append(join_func)

    return body[:i] + dispatch_block


# ── helpers ────────────────────────────────────────────────────────────


def _extract_gather_target(
    stmt: cst.SimpleStatementLine,
) -> tuple[str | None, cst.Tuple | None, cst.Call]:
    """Pull out (target_name, tuple_target, gather_call) from the stmt's element."""
    element = stmt.body[0]
    tuple_target: cst.Tuple | None = None
    target_name: str | None = None

    if isinstance(element, cst.Assign):
        target = element.targets[0].target
        if isinstance(target, cst.Name):
            target_name = target.value
        elif isinstance(target, cst.Tuple):
            tuple_target = target
        else:
            msg = f"gather() result must be a Name or Tuple, got {type(target).__name__}"
            raise ValueError(msg)
        return target_name, tuple_target, element.value

    if isinstance(element, cst.Expr):
        return None, None, element.value

    msg = f"Unsupported gather statement element: {type(element).__name__}"
    raise ValueError(msg)


def _resolve_arg_call(call_node: cst.CSTNode, ctx_label: str) -> tuple[cst.BaseExpression, str]:
    """Validate a gather argument call shape and return (receiver, method).

    Constructor calls map to method 'insert'; method calls return the attr name.
    """
    if not isinstance(call_node, cst.Call):
        msg = f"{ctx_label} arguments must be method calls, got {type(call_node).__name__}"
        raise ValueError(msg)
    if isinstance(call_node.func, cst.Name):
        return call_node.func, "insert"
    if isinstance(call_node.func, cst.Attribute):
        return call_node.func.value, call_node.func.attr.value
    msg = f"Unsupported {ctx_label} argument call type: {type(call_node.func).__name__}"
    raise ValueError(msg)


def _validate_entity_call(receiver: cst.BaseExpression, method: str, entities: dict, ctx_label: str) -> None:
    if method == "insert" and isinstance(receiver, cst.Name) and receiver.value not in entities:
        msg = f"{ctx_label} elements must be entity methods or constructors, got plain function '{receiver.value}'"
        raise ValueError(msg)


def _build_static_dispatch(ctx: SplitContext, gather_args: list, join_name: str, vars_to_save: set | None) -> list:
    reply_op_name = ctx.op_name
    save_set = vars_to_save if vars_to_save is not None else ctx.defined_vars
    sorted_vars = sorted(ctx.liveness.simple_name(v) for v in save_set)
    saved_dict = context_dict(sorted_vars)

    statements: list = [init_gather_barrier_stmt(cst.Integer(str(len(gather_args))), saved_dict)]

    for tag, call_node in enumerate(gather_args):
        receiver, method = _resolve_arg_call(call_node, "gather()")
        _validate_entity_call(receiver, method, ctx.entities, "gather()")
        target_op = ctx.resolver.operator_name_for(receiver)
        key_value = ctx.resolver.key_for_call(receiver, call_node, method)

        join_context = dict_from_pairs(
            [
                ("_g_barrier", cst.Name("_gather_id")),
                ("_g_tag", cst.Integer(str(tag))),
            ]
        )
        child_reply_list = cst.List(
            elements=[cst.Element(value=reply_entry(reply_op_name, join_name, CTX_KEY, join_context))]
        )

        reply_to_var = f"_g_reply_{tag}"
        reply_assign = assign_stmt(cst.Name(reply_to_var), child_reply_list)

        original_args = [arg.value for arg in call_node.args]
        params_value = params_with_reply_to(original_args, cst.Name(reply_to_var))
        async_call = call_remote_async_stmt(target_op, method, key_value, params_value)

        statements.extend([reply_assign, async_call])

    return statements


def _build_spread_dispatch(
    ctx: SplitContext,
    comp: cst.ListComp | cst.SetComp | cst.GeneratorExp,
    join_name: str,
    vars_to_save: set | None,
) -> list:
    if comp.for_in.inner_for_in is not None or comp.for_in.ifs:
        # TODO: Add support for filters and multiple for-clauses in comprehensions.
        msg = "gather(*<comp>) currently only supports a single, unfiltered for-clause"
        raise ValueError(msg)

    call_node = comp.elt
    receiver, method = _resolve_arg_call(call_node, "gather(*<comp>)")
    _validate_entity_call(receiver, method, ctx.entities, "gather(*<comp>)")

    target_op = ctx.resolver.operator_name_for(receiver)
    key_value = ctx.resolver.key_for_call(receiver, call_node, method)

    reply_op_name = ctx.op_name
    save_set = vars_to_save if vars_to_save is not None else ctx.defined_vars
    sorted_vars = sorted(ctx.liveness.simple_name(v) for v in save_set)
    saved_dict = context_dict(sorted_vars)

    # _g_iter = list(<comp.iter>) — materialize so we can take len() and iterate
    # again. Covers enumerate/zip/range/generators safely.
    materialize_stmt = assign_stmt(
        cst.Name("_g_iter"),
        cst.Call(func=cst.Name("list"), args=[cst.Arg(value=comp.for_in.iter)]),
    )
    init_stmt = init_gather_barrier_stmt(
        cst.Call(func=cst.Name("len"), args=[cst.Arg(value=cst.Name("_g_iter"))]),
        saved_dict,
    )

    # for _g_tag, <comp.target> in enumerate(_g_iter):
    #     _g_reply = [{...join entry...}]; ctx.call_remote_async(...)
    # Tuple targets like need parentheses so that they match with enumerate.
    comp_target = comp.for_in.target
    if isinstance(comp_target, cst.Tuple) and not comp_target.lpar:
        comp_target = comp_target.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()])
    loop_target = cst.Tuple(elements=[cst.Element(value=cst.Name("_g_tag")), cst.Element(value=comp_target)])
    loop_iter = cst.Call(func=cst.Name("enumerate"), args=[cst.Arg(value=cst.Name("_g_iter"))])

    join_context = dict_from_pairs(
        [
            ("_g_barrier", cst.Name("_gather_id")),
            ("_g_tag", cst.Name("_g_tag")),
        ]
    )
    reply_assign = assign_stmt(
        cst.Name("_g_reply"),
        cst.List(elements=[cst.Element(value=reply_entry(reply_op_name, join_name, CTX_KEY, join_context))]),
    )

    original_args = [arg.value for arg in call_node.args]
    params_value = params_with_reply_to(original_args, cst.Name("_g_reply"))
    async_call = call_remote_async_stmt(target_op, method, key_value, params_value)

    dispatch_loop = cst.For(
        target=loop_target,
        iter=loop_iter,
        body=cst.IndentedBlock(body=[reply_assign, async_call]),
    )

    # Empty iterable → zero dispatches → nothing would ever call
    # update_gather_barrier, so the join (and thus the reply) never fires.
    # Fire a single self-call to the join; the total==0 barrier resolves it
    # immediately with empty results.
    empty_context = dict_from_pairs([("_g_barrier", cst.Name("_gather_id")), ("_g_tag", cst.Integer("0"))])
    empty_call = call_remote_async_stmt(
        reply_op_name,
        join_name,
        CTX_KEY,
        params_with_reply_to([empty_context, cst.Name("None")], cst.Name("None")),
    )
    is_empty = cst.Comparison(
        left=cst.Call(func=cst.Name("len"), args=[cst.Arg(value=cst.Name("_g_iter"))]),
        comparisons=[cst.ComparisonTarget(operator=cst.Equal(), comparator=cst.Integer("0"))],
    )
    guard = cst.If(
        test=is_empty,
        body=cst.IndentedBlock(body=[empty_call]),
        orelse=cst.Else(body=cst.IndentedBlock(body=[dispatch_loop])),
    )
    return [materialize_stmt, init_stmt, guard]


def _build_join_body(
    ctx: SplitContext,
    target_name: str | None,
    tuple_target: cst.Tuple | None,
    vars_to_save: set | None,
    post_split: list,
    loop_context: LoopContext | None,
) -> list:
    """Body of the gather join continuation."""
    save_set = vars_to_save if vars_to_save is not None else ctx.defined_vars
    sorted_vars = sorted(ctx.liveness.simple_name(v) for v in save_set)

    body_stmts: list = [
        cst.parse_statement("barrier_id = func_context['_g_barrier']"),
        cst.parse_statement("_g_tag = func_context['_g_tag']"),
        cst.parse_statement(
            "(is_complete, _g_results, saved, parent_reply_to) = "
            "update_gather_barrier(ctx, barrier_id, _g_tag, _gather_partial)"
        ),
        cst.parse_statement("if not is_complete:\n    return\n"),
    ]

    # Restore locals from `saved` dict (direct .get() — no resolve_context here).
    if sorted_vars:
        body_stmts.append(restore_tuple_from("saved", sorted_vars))

    body_stmts.append(cst.parse_statement("reply_to = parent_reply_to"))

    bind_target: cst.BaseExpression | None = tuple_target or (
        cst.Name(target_name) if target_name is not None else None
    )
    if bind_target is not None:
        body_stmts.append(assign_stmt(bind_target, cst.Name("_g_results")))

    body_stmts.extend(ctx.split_body(list(post_split), loop_context))
    return body_stmts
