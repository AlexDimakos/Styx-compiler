"""Splitting at an if-statement whose branches contain remote calls.

The if/elif/else structure is preserved, but each branch's body is recursively
split. When the if-branch dispatches but there's no else, the post-if statements
are folded into a synthetic else to prevent fall-through. When inside a loop, a missing else with
no post-if becomes an explicit loop-back jump.
"""

import libcst as cst

from styx_compiler.predicates import any_remote_call, ends_with_terminator
from styx_compiler.splitting.context import LoopContext, SplitContext


def handle_if(ctx: SplitContext, body: list, i: int, loop_context: LoopContext | None = None) -> list:
    """Split at an if-statement (at index i) whose branches contain remote calls."""
    pre_if = body[:i]
    result = _process_if_node(ctx, body[i], body[i + 1 :], loop_context)
    return pre_if + result


def _process_if_node(
    ctx: SplitContext,
    if_stmt: cst.If,
    post_if: list,
    loop_context: LoopContext | None = None,
) -> list:
    """Recursively process an if/elif node. Returns a list of statements."""
    if_body_stmts = list(if_stmt.body.body)
    if_branch_dispatches = any_remote_call(ctx.resolver, if_body_stmts, loop_context)

    # Snapshot before branching — each branch starts from the same defined_vars.
    # This way variables defined in one branch aren't considered live in the other.
    saved_vars = ctx.defined_vars.copy()

    # If a branch already ends in `return`/`raise`, post_if is dead code.
    if_tail = [] if ends_with_terminator(if_body_stmts) else post_if
    new_if_body = ctx.split_body(if_body_stmts + if_tail, loop_context)

    # Restore for else/elif branch.
    ctx.defined_vars = saved_vars.copy()

    new_else: cst.Else | cst.If | None = None
    if if_stmt.orelse is not None:
        if isinstance(if_stmt.orelse, cst.Else):
            else_body_stmts = list(if_stmt.orelse.body.body)
            else_tail = [] if ends_with_terminator(else_body_stmts) else post_if
            new_else_body = ctx.split_body(else_body_stmts + else_tail, loop_context)
            new_else = cst.Else(body=cst.IndentedBlock(body=new_else_body))
        elif isinstance(if_stmt.orelse, cst.If):
            # elif chain
            elif_result = _process_if_node(ctx, if_stmt.orelse, post_if, loop_context)
            if len(elif_result) == 1 and isinstance(elif_result[0], cst.If):
                new_else = elif_result[0]
            else:
                new_else = cst.Else(body=cst.IndentedBlock(body=elif_result))
    elif if_branch_dispatches and post_if:
        # No else but if-branch dispatches — fold post-if into else to prevent fallthrough.
        processed_post_if = ctx.split_body(post_if, loop_context)
        new_else = cst.Else(body=cst.IndentedBlock(body=processed_post_if))
    elif if_branch_dispatches and not post_if and loop_context:
        # No else, no post-if, inside a loop — the false path must loop back.
        vars_to_save = loop_context.continue_save_vars & ctx.defined_vars
        ctx.liveness.add_synthetic_loop_vars(vars_to_save, ctx.defined_vars)
        loop_back = ctx.direct_continuation_call(loop_context.loop_step_name, vars_to_save)
        new_else = cst.Else(body=cst.IndentedBlock(body=[loop_back]))

    # Restore to pre-branch state — the caller decides what's defined after the if.
    ctx.defined_vars = saved_vars

    new_if = if_stmt.with_changes(body=cst.IndentedBlock(body=new_if_body), orelse=new_else)

    if if_stmt.orelse is not None or (if_branch_dispatches and (post_if or loop_context)):
        return [new_if]
    return [new_if, *post_if]
