"""Splitting at a single remote call statement.

Each dispatch is a pair of statements:
  reply_to = push_continuation(...)
  ctx.call_remote_async(...)

In the post-split body we use another helper to restore live variables from the context dict:
  params = resolve_context(...)
"""

import libcst as cst

from styx_compiler.cst_helpers import assign_stmt
from styx_compiler.splitting.context import LoopContext, SplitContext


def handle_remote_call(ctx: SplitContext, body: list, i: int, loop_context: LoopContext | None = None) -> list:
    stmt = body[i]
    post_split = body[i + 1 :]
    has_continuation = len(post_split) > 0

    target_var, call_node, receiver, remote_method, tuple_target = extract_call_info(stmt)

    # Tail-call optimization: if the only follow-up is `return <target_var>`,
    # skip the continuation — the reply already flows back via reply_to.
    if (
        len(post_split) == 1
        and isinstance(post_split[0], cst.SimpleStatementLine)
        and len(post_split[0].body) == 1
        and isinstance(post_split[0].body[0], cst.Return)
    ):
        ret_stmt = post_split[0].body[0]
        if isinstance(ret_stmt.value, cst.Name) and ret_stmt.value.value == target_var:
            has_continuation = False

    vars_to_save = ctx.liveness.vars_to_save_at(stmt, ctx.defined_vars)

    if has_continuation:
        next_func_name = ctx.next_step_name()

        dispatch_block = ctx.dispatch_block(receiver, remote_method, call_node, next_func_name, vars_to_save)
        restore_block = ctx.restore_block(vars_to_save)

        # Tuple unpacking continuation: inject `(a, b) = __tuple_result` and
        # add each element to defined_vars so the rest of the body knows them.
        unpack_stmts = []
        if tuple_target is not None:
            for el in tuple_target.elements:
                if isinstance(el.value, cst.Name):
                    ctx.defined_vars.add(el.value.value)
            unpack_stmts.append(assign_stmt(tuple_target, cst.Name(target_var)))
        elif target_var != "placeholder_return":
            ctx.defined_vars.add(target_var)

        cont_body = restore_block + unpack_stmts + ctx.split_body(post_split, loop_context)
        cont_func = ctx.make_continuation(next_func_name, cont_body, target_var)
        ctx.generated_functions.append(cont_func)

        return body[:i] + dispatch_block

    # Last remote call — either jump back to a loop step or fall through.
    next_target = loop_context.loop_step_name if loop_context else "None"
    dispatch_block = ctx.dispatch_block(receiver, remote_method, call_node, next_target, vars_to_save)
    return body[:i] + dispatch_block


def extract_call_info(
    stmt: cst.SimpleStatementLine,
) -> tuple[str, cst.Call, cst.BaseExpression, str, cst.Tuple | None]:
    """Decompose `<target> = <recv>.<method>(args)` (or its variants).

    Returns (target_var, call_node, receiver, method, tuple_target).
    """
    element = stmt.body[0]
    tuple_target: cst.Tuple | None = None

    if isinstance(element, cst.Assign):
        target = element.targets[0].target
        if isinstance(target, cst.Tuple):
            target_var = "__tuple_result"
            tuple_target = target
        else:
            target_var = target.value
        call_node = element.value
    elif isinstance(element, cst.Expr):
        target_var = "placeholder_return"
        call_node = element.value
    else:
        msg = f"Unexpected element: {type(element)}"
        raise ValueError(msg)

    if isinstance(call_node.func, cst.Name):
        receiver = call_node.func
        remote_method = "insert"
    elif isinstance(call_node.func, cst.Attribute):
        receiver = call_node.func.value
        remote_method = call_node.func.attr.value
    else:
        msg = f"Unsupported call type: {type(call_node.func)}"
        raise ValueError(msg)

    return target_var, call_node, receiver, remote_method, tuple_target
