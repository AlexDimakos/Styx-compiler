"""Shared mutable state + the top-level dispatch loop for the splitter.

`SplitContext` carries everything the per-construct handlers need to read and
mutate as they walk the function body:

  - the entity registry / class name / function name (for naming step funcs)
  - counters (`split_counter`, `loop_iter_counter`)
  - `defined_vars` — the live set of in-scope locals at the current point;
    rebound at branch boundaries by handlers (so we hold it on the dataclass,
    not by reference elsewhere)
  - `generated_functions` — the list of step functions produced so far
  - the `EntityResolver` and `LivenessHelper` adapters
  - shared continuation-builder methods (`direct_continuation_call`,
    `dispatch_block`, `restore_block`, `make_continuation`)
  - `split_body` — the dispatch loop itself, called recursively from handlers
"""

from typing import NamedTuple

import libcst as cst

from styx_compiler.cst_helpers import (
    CTX_KEY,
    call_remote_async_stmt,
    context_dict,
    continuation_func,
    continuation_params,
    params_with_reply_to,
    push_continuation_stmt,
    resolve_context_stmt,
    restore_tuple_from,
)
from styx_compiler.entity_resolver import EntityResolver
from styx_compiler.liveness import LivenessHelper
from styx_compiler.predicates import (
    ends_with_raise,
    for_contains_remote_call,
    if_contains_remote_call,
    is_break,
    is_continue,
    is_gather,
    while_contains_remote_call,
)


class LoopContext(NamedTuple):
    """Carried while processing a loop body that contains splits.

    Tells nested handlers where `continue` and `break` should jump and what
    state they need to ship along.
    """

    loop_step_name: str
    op_name: str
    iter_var_name: str | None
    post_loop_func_name: str | None
    # Vars to save when re-entering the loop step (continue + tail back-edges).
    continue_save_vars: set[str]
    # Vars to save when exiting to the post-loop step (break). None if no post-loop.
    break_save_vars: set[str] | None


class SplitContext:
    def __init__(
        self,
        func_name: str,
        class_name: str,
        entities: dict[str, str],
        resolver: EntityResolver,
        liveness: LivenessHelper,
    ):
        self.func_name = func_name
        self.class_name = class_name
        self.entities = entities
        self.resolver = resolver
        self.liveness = liveness

        self.split_counter = 1
        self.loop_iter_counter = 0
        self.defined_vars: set[str] = set()
        self.generated_functions: list[cst.FunctionDef] = []

    # ── helpers ───────────────────────────────────────────────

    def next_step_name(self) -> str:
        self.split_counter += 1
        return f"{self.func_name}_step_{self.split_counter}"

    def next_loop_iter_var(self) -> str:
        self.loop_iter_counter += 1
        return f"__loop_index_{self.loop_iter_counter}"

    @property
    def op_name(self) -> str:
        return self.entities[self.class_name]

    # ── continuation builders ─────────────────────────────────

    def direct_continuation_call(self, func_name: str, vars_to_save: set | None = None) -> cst.SimpleStatementLine:
        """ctx.call_remote_async to a continuation on this same entity, with
        no reply_to push (used for loop back-edges, break/continue jumps,
        post-loop entry, and the initial loop-step call).
        """
        save_set = vars_to_save if vars_to_save is not None else self.defined_vars
        sorted_vars = sorted(self.liveness.simple_name(v) for v in save_set)
        ctx_dict = context_dict(sorted_vars)
        params = continuation_params(ctx_dict, cst.Name("None"), cst.Name("reply_to"))
        return call_remote_async_stmt(self.op_name, func_name, CTX_KEY, params)

    def dispatch_block(
        self,
        receiver: cst.BaseExpression,
        method: str,
        call_node: cst.Call,
        next_func_name: str,
        vars_to_save: set | None = None,
    ) -> list:
        """Build [push_continuation, call_remote_async] for a regular split.

        If `next_func_name == "None"`, no continuation push is emitted.
        """
        key_value = self.resolver.key_for_call(receiver, call_node, method)
        original_args = [arg.value for arg in call_node.args]
        params_value = params_with_reply_to(original_args, cst.Name("reply_to"))

        target_op = self.resolver.operator_name_for(receiver)
        async_call = call_remote_async_stmt(target_op, method, key_value, params_value)

        if next_func_name == "None":
            return [async_call]

        save_set = vars_to_save if vars_to_save is not None else self.defined_vars
        sorted_vars = sorted(self.liveness.simple_name(v) for v in save_set)
        ctx_dict = context_dict(sorted_vars)

        push_call = push_continuation_stmt(self.op_name, next_func_name, ctx_dict)
        return [push_call, async_call]

    def restore_block(self, vars_to_restore: set | None = None) -> list:
        """params = resolve_context(...); (v1, v2, ...) = (params.get('v1'), ...)."""
        vars_source = vars_to_restore if vars_to_restore is not None else self.defined_vars
        sorted_vars = sorted(self.liveness.simple_name(v) for v in vars_source)
        if not sorted_vars:
            return []
        return [resolve_context_stmt(), restore_tuple_from("params", sorted_vars)]

    def make_continuation(self, name: str, body: list, target_var: str) -> cst.FunctionDef:
        return continuation_func(name, body, target_var, self.op_name)

    def track_vars(self, stmt: cst.CSTNode) -> None:
        """Add any variables assigned (recursively) within stmt to defined_vars."""
        self.defined_vars.update(LivenessHelper.collect_assigned_vars([stmt]))

    # ── top-level dispatch loop ──────────────────────────────────────

    def split_body(self, body: list, loop_context: LoopContext | None = None) -> list:
        # Imported lazily to break the import cycle
        from styx_compiler.splitting.gather import handle_gather  # noqa: PLC0415
        from styx_compiler.splitting.if_split import handle_if  # noqa: PLC0415
        from styx_compiler.splitting.loop import handle_loop  # noqa: PLC0415
        from styx_compiler.splitting.remote_call import handle_remote_call  # noqa: PLC0415

        for i, stmt in enumerate(body):
            # gather(...) → fan-out + barrier join
            if is_gather(stmt):
                return handle_gather(self, body, i, loop_context)

            # continue → jump back to loop step
            if is_continue(stmt) and loop_context:
                vars_to_save = loop_context.continue_save_vars & self.defined_vars
                self.liveness.add_synthetic_loop_vars(vars_to_save, self.defined_vars)
                direct = self.direct_continuation_call(loop_context.loop_step_name, vars_to_save)
                return [*body[:i], direct]

            # break → jump to post-loop continuation (or return if none)
            if is_break(stmt) and loop_context:
                if loop_context.post_loop_func_name and loop_context.break_save_vars is not None:
                    vars_to_save = loop_context.break_save_vars & self.defined_vars
                    self.liveness.add_synthetic_loop_vars(vars_to_save, self.defined_vars)
                    jump = self.direct_continuation_call(loop_context.post_loop_func_name, vars_to_save)
                else:
                    jump = cst.SimpleStatementLine(body=[cst.Return(value=None)])
                return [*body[:i], jump]

            if self.resolver.is_remote_call(stmt):
                return handle_remote_call(self, body, i, loop_context)

            if isinstance(stmt, cst.If) and if_contains_remote_call(self.resolver, stmt, loop_context):
                return handle_if(self, body, i, loop_context)

            if isinstance(stmt, cst.For) and for_contains_remote_call(self.resolver, stmt):
                return handle_loop(self, body, i, loop_context)

            if isinstance(stmt, cst.While) and while_contains_remote_call(self.resolver, stmt):
                return handle_loop(self, body, i, loop_context)

            self.track_vars(stmt)

        # No remote calls found — tail of a loop body or normal function end.
        if loop_context:
            if body and ends_with_raise(body):
                return body
            # Tail back to loop step, pass live-in at loop header.
            vars_to_save = loop_context.continue_save_vars & self.defined_vars
            self.liveness.add_synthetic_loop_vars(vars_to_save, self.defined_vars)
            direct_call = self.direct_continuation_call(loop_context.loop_step_name, vars_to_save)
            return [*body, direct_call]
        return body
