"""Splitting at a for/while-loop whose body contains remote calls.

Loops are turned into three functions: the pre-loop code,
a loop step, and the post-loop code. The loop step is an if/else statement
that checks the loop bounds/test and either dispatches the body or jumps to the post-loop continuation.
NOTE we need a post-loop continuation for break statements
NOTE while loops that have remote calls in the condition are translated to while True with an if condition: break

Four iterator shapes are currently supported for `for`:
    - `for x in range(n)` and `for x in range(start, stop)` → bounds via range args
    - `for a, b in zip(xs, ys)` → bound = len(xs); per-iteration assigns a = xs[i], b = ys[i]
    - `for x in items` → bound = len(items); x = items[i]
    - `for i, x in enumerate(items)` → bound = len(items); i = idx[+start]; x = items[idx]
"""

import libcst as cst

from styx_compiler.cst_helpers import assign_stmt
from styx_compiler.liveness import LivenessHelper
from styx_compiler.splitting.context import LoopContext, SplitContext


def handle_loop(ctx: SplitContext, body: list, i: int, loop_context: LoopContext | None = None) -> list:
    """Split at a for/while-loop whose body contains remote calls."""
    loop_stmt = body[i]
    pre_loop = body[:i]
    post_loop = body[i + 1 :]

    is_for = isinstance(loop_stmt, cst.For)
    loop_step_name = ctx.next_step_name()

    # For-loop initialization (None for While).
    init_iter = None
    var_assigns: list | None = None
    inc_idx = None
    loop_var_name = "_loop_var"
    extra_loop_vars: list[str] = []
    bound_expr = None
    state_idx_access = None

    if is_for:
        iter_var_name = ctx.next_loop_iter_var()
        state_idx_access = cst.Name(iter_var_name)
        init_iter, var_assigns, inc_idx, bound_expr, loop_var_name, extra_loop_vars = _build_for_iter(
            ctx, loop_stmt, iter_var_name, state_idx_access
        )

    # Pre-scan loop body assignments — these may flow into the loop step via
    # the back-edge (in addition to anything from outside the loop).
    loop_body_stmts = list(loop_stmt.body.body)
    loop_body_vars = LivenessHelper.collect_assigned_vars(loop_body_stmts)

    known_vars = set(ctx.defined_vars) | loop_body_vars

    # Live-in at loop entry: what's needed to (re-)evaluate iter/test and body.
    live_in = ctx.liveness.live_in_at_loop(loop_stmt) if isinstance(loop_stmt, (cst.For, cst.While)) else None
    if live_in is not None:
        entry_save_vars = live_in & known_vars
        ctx.liveness.add_synthetic_loop_vars(entry_save_vars, ctx.defined_vars)
    else:
        entry_save_vars = set(known_vars)

    # Live-out of the loop: an upper bound on what the post-loop code needs.
    live_out = ctx.liveness.live_out_at_loop(loop_stmt) if isinstance(loop_stmt, (cst.For, cst.While)) else None
    if live_out is not None:
        post_loop_save_vars = live_out & known_vars
        ctx.liveness.add_synthetic_loop_vars(post_loop_save_vars, ctx.defined_vars)
    else:
        post_loop_save_vars = set(known_vars)

    # Initial entry into the loop: only the outside knows about pre-loop vars.
    vars_for_loop_entry = entry_save_vars & ctx.defined_vars
    ctx.liveness.add_synthetic_loop_vars(vars_for_loop_entry, ctx.defined_vars)
    direct_call = ctx.direct_continuation_call(loop_step_name, vars_for_loop_entry)

    # Restore at the start of the loop step covers everything any incoming edge
    # (initial entry or body back-edge) might pass.
    restore_block = ctx.restore_block(entry_save_vars)

    saved_vars = ctx.defined_vars.copy()

    # Post-loop code: processed with the OUTER loop_context (not this loop's).
    if post_loop:
        post_loop_func_name = ctx.next_step_name()
        post_loop_body = ctx.split_body(post_loop, loop_context)
        post_loop_restore = ctx.restore_block(post_loop_save_vars)
        post_loop_func = ctx.make_continuation(
            post_loop_func_name, post_loop_restore + post_loop_body, "placeholder_return"
        )
        ctx.generated_functions.append(post_loop_func)
        exit_stmts = [ctx.direct_continuation_call(post_loop_func_name, post_loop_save_vars)]
    else:
        post_loop_func_name = None
        exit_stmts = [cst.SimpleStatementLine(body=[cst.Return(value=None)])]

    # Restore before processing loop body (independent path).
    ctx.defined_vars = saved_vars.copy()

    if is_for and loop_var_name != "_loop_var":
        ctx.defined_vars.add(loop_var_name)
    for v in extra_loop_vars:  # additional zip tuple-target variables
        ctx.defined_vars.add(v)

    inner_loop_context = LoopContext(
        loop_step_name=loop_step_name,
        op_name=ctx.op_name,
        iter_var_name=state_idx_access.value if is_for else None,
        post_loop_func_name=post_loop_func_name,
        continue_save_vars=entry_save_vars,
        break_save_vars=post_loop_save_vars if post_loop else None,
    )
    loop_body_processed = ctx.split_body(loop_body_stmts, inner_loop_context)

    # Restore to pre-branch state.
    ctx.defined_vars = saved_vars

    if is_for:
        # if __loop_index >= bound: <exit>; else: <var assigns>; <inc>; <body>
        test_cond = cst.Comparison(
            left=state_idx_access,
            comparisons=[cst.ComparisonTarget(operator=cst.GreaterThanEqual(), comparator=bound_expr)],
        )
        body_block = cst.IndentedBlock(body=exit_stmts)
        else_block = cst.Else(body=cst.IndentedBlock(body=[*(var_assigns or []), inc_idx, *loop_body_processed]))
        if_block = cst.If(test=test_cond, body=body_block, orelse=else_block)
        loop_step_body = [*restore_block, if_block]
    else:
        test_cond = loop_stmt.test
        is_while_true = isinstance(test_cond, cst.Name) and test_cond.value == "True"
        if is_while_true:
            # `while True:` — the body itself handles exit via break.
            loop_step_body = [*restore_block, *loop_body_processed]
        else:
            body_block = cst.IndentedBlock(body=loop_body_processed)
            else_block = cst.Else(body=cst.IndentedBlock(body=exit_stmts))
            if_block = cst.If(test=test_cond, body=body_block, orelse=else_block)
            loop_step_body = [*restore_block, if_block]

    cont_func = ctx.make_continuation(loop_step_name, loop_step_body, "placeholder_return")
    ctx.generated_functions.append(cont_func)

    if is_for:
        return [*pre_loop, init_iter, direct_call]
    return [*pre_loop, direct_call]


# ── for-iterator setup ─────────────────────────────────────────────────


def _build_for_iter(
    ctx: SplitContext,
    loop_stmt: cst.For,
    iter_var_name: str,
    state_idx_access: cst.Name,
) -> tuple[
    cst.SimpleStatementLine,  # init_iter:  __loop_index = <start>
    list,  # var_assigns: per-iteration var bindings
    cst.SimpleStatementLine,  # inc_idx:    __loop_index += 1
    cst.BaseExpression,  # bound_expr: stop value for the bounds check
    str,  # loop_var_name (or "_loop_var" if no name target)
    list[str],  # extra_loop_vars (zip tuple targets after the first)
]:
    iter_node = loop_stmt.iter
    is_call = isinstance(iter_node, cst.Call) and isinstance(iter_node.func, cst.Name)
    is_zip_iter = is_call and iter_node.func.value == "zip"
    is_enumerate_iter = is_call and iter_node.func.value == "enumerate"

    if is_enumerate_iter:
        # for i, x in enumerate(items[, start]): bound = len(items); i = idx[+start]; x = items[idx]
        enum_args = [arg.value for arg in iter_node.args]
        iterable = enum_args[0]
        start = enum_args[1] if len(enum_args) >= 2 else None
        bound_expr = cst.Call(func=cst.Name("len"), args=[cst.Arg(value=iterable)])
        init_iter = assign_stmt(cst.Name(iter_var_name), cst.Integer("0"))
        ctx.defined_vars.add(iter_var_name)

        target = loop_stmt.target
        if not isinstance(target, cst.Tuple) or len(target.elements) != 2:
            msg = "enumerate() loop target must unpack to two names, e.g. `for i, x in enumerate(items):`"
            raise ValueError(msg)
        idx_name, val_name = (el.value.value for el in target.elements)
        idx_value = (
            state_idx_access
            if start is None
            else cst.BinaryOperation(left=state_idx_access, operator=cst.Add(), right=start)
        )
        var_assigns = [
            assign_stmt(cst.Name(idx_name), idx_value),
            assign_stmt(
                cst.Name(val_name),
                cst.Subscript(
                    value=iterable,
                    slice=[cst.SubscriptElement(slice=cst.Index(value=state_idx_access))],
                ),
            ),
        ]
        loop_var_name, extra_loop_vars = idx_name, [val_name]
    elif is_zip_iter:
        # for a, b in zip(xs, ys): bound = len(xs); a = xs[i]; b = ys[i]
        zip_args = [arg.value for arg in iter_node.args]
        bound_expr = cst.Call(func=cst.Name("len"), args=[cst.Arg(value=zip_args[0])])

        init_iter = assign_stmt(cst.Name(iter_var_name), cst.Integer("0"))
        ctx.defined_vars.add(iter_var_name)

        target = loop_stmt.target
        if isinstance(target, cst.Tuple):
            target_names = [elem.value.value for elem in target.elements if isinstance(elem.value, cst.Name)]
        elif isinstance(target, cst.Name):
            target_names = [target.value]
        else:
            target_names = []

        var_assigns = [
            assign_stmt(
                cst.Name(var_name),
                cst.Subscript(
                    value=zip_arg,
                    slice=[cst.SubscriptElement(slice=cst.Index(value=state_idx_access))],
                ),
            )
            for zip_arg, var_name in zip(zip_args, target_names, strict=False)
        ]

        loop_var_name = target_names[0] if target_names else "_loop_var"
        extra_loop_vars = target_names[1:]
    else:
        start_expr, bound_expr, is_range = _parse_loop_iter(iter_node)
        init_iter = assign_stmt(cst.Name(iter_var_name), start_expr)
        ctx.defined_vars.add(iter_var_name)

        loop_var_name = "_loop_var"
        if isinstance(loop_stmt.target, cst.Name):
            loop_var_name = loop_stmt.target.value

        if is_range:
            var_val = state_idx_access
        else:
            var_val = cst.Subscript(
                value=iter_node,
                slice=[cst.SubscriptElement(slice=cst.Index(value=state_idx_access))],
            )
        var_assigns = [assign_stmt(cst.Name(loop_var_name), var_val)]
        extra_loop_vars = []

    inc_idx = cst.SimpleStatementLine(
        body=[cst.AugAssign(target=state_idx_access, operator=cst.AddAssign(), value=cst.Integer("1"))]
    )

    return init_iter, var_assigns, inc_idx, bound_expr, loop_var_name, extra_loop_vars


def _parse_loop_iter(iter_node: cst.BaseExpression) -> tuple[cst.BaseExpression, cst.BaseExpression, bool]:
    """Return (start, bound, is_range). Supports range() and collection iteration."""
    if isinstance(iter_node, cst.Call) and isinstance(iter_node.func, cst.Name) and iter_node.func.value == "range":
        if len(iter_node.args) == 1:
            return cst.Integer("0"), iter_node.args[0].value, True
        if len(iter_node.args) >= 2:
            return iter_node.args[0].value, iter_node.args[1].value, True

    bound = cst.Call(func=cst.Name("len"), args=[cst.Arg(value=iter_node)])
    return cst.Integer("0"), bound, False
