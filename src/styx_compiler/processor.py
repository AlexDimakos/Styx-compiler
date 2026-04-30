from collections.abc import Mapping
from typing import NamedTuple

import libcst as cst
from libcst import matchers as m

from styx_compiler.cst_helpers import (
    CTX_KEY,
    assign_stmt,
    call_remote_async_stmt,
    context_dict,
    continuation_func,
    continuation_params,
    dict_from_pairs,
    init_gather_barrier_stmt,
    params_with_reply_to,
    push_continuation_stmt,
    reply_entry,
    resolve_context_stmt,
    restore_tuple_from,
    sink_reply_to,
)


class LoopContext(NamedTuple):
    """Context passed down while processing the body of a loop that contains splits."""

    loop_step_name: str
    op_name: str
    iter_var_name: str | None
    post_loop_func_name: str | None
    # Vars to save when re-entering the loop step (for `continue` and tail back-edges).
    continue_save_vars: set[str]
    # Vars to save when exiting to the post-loop step (for `break`). None if no post-loop.
    break_save_vars: set[str] | None


class FunctionProcessor:
    """Slices a function into asynchronous step functions across remote-call boundaries."""

    def __init__(
        self,
        original_func: cst.FunctionDef,
        class_name: str,
        entities: dict[str, str],
        metadata: Mapping,
        entity_keys: dict[str, list[str]] | None = None,
        entity_init_params: dict[str, list[str]] | None = None,
        live_vars: Mapping | None = None,
    ):
        self.original_func = original_func
        self.class_name = class_name

        # Track entities marked with @entity decorator
        self.entities = entities
        self.entity_keys = entity_keys or {}
        self.entity_init_params = entity_init_params or {}

        # Mypy metadata: cst.CSTNode -> MypyType
        self.metadata = metadata

        # Live variable metadata: cst.CSTNode -> (live_in, live_out)
        self.live_vars = dict(live_vars) if live_vars is not None else {}

        self.split_counter = 1
        self.loop_iter_counter = 0
        self.generated_functions: list[cst.FunctionDef] = []

        # Track local variables to save/restore
        self.defined_vars = set()

        # Pre-scan arguments to define variables
        for param in original_func.params.params:
            if param.name.value != "self" and param.name.value != "ctx":
                self.defined_vars.add(param.name.value)

    def _get_simple_name(self, v) -> str:
        """Extract a simple string name from a string, QualifiedName, or dataflow symbolic value."""
        if hasattr(v, "name"):
            # QualifiedName's .name may be 'module.func.<locals>.var'
            return str(v.name).split(".")[-1]
        # Fallback for strings or SymbolicTop/Bottom
        return str(v).split(".")[-1] if "." in str(v) else str(v)

    def _live_set_from_node(self, node: cst.CSTNode | None, kind: str) -> frozenset | None:
        """Get live-in (kind='in') or live-out (kind='out') for a CST node, or None."""
        if not self.live_vars or node is None:
            return None
        data = self.live_vars.get(node)
        if data is None:
            return None
        val = data[0] if kind == "in" else data[1]
        return val if isinstance(val, frozenset) else None

    def _get_live_out(self, stmt: cst.CSTNode) -> frozenset | None:
        """
        Look up the live-out set for a statement from the live variable metadata.
        Returns None if no liveness data is available for this node.
        """
        # For SimpleStatementLine: look up the inner assignment/expression node
        if isinstance(stmt, cst.SimpleStatementLine) and stmt.body:
            element = stmt.body[0]
            if isinstance(element, cst.Assign) and element.targets:
                live = self._live_set_from_node(element.targets[0], "out")
                if live is not None:
                    return live
            elif isinstance(element, cst.Expr) and isinstance(element.value, cst.Call):
                live = self._live_set_from_node(element.value, "out")
                if live is not None:
                    return live
            elif isinstance(element, cst.AugAssign):
                live = self._live_set_from_node(element, "out")
                if live is not None:
                    return live

        return self._live_set_from_node(stmt, "out")

    def _live_in_at_loop(self, loop_stmt: cst.For | cst.While) -> set[str] | None:
        """
        Vars live just before (re-)entering a loop iteration. For `for x in xs:` the
        For node's live-in is body-live-in minus the loop target, so we add iter free
        names back. For While, no statement-level CFG node exists.
        """
        if isinstance(loop_stmt, cst.For):
            live = self._live_set_from_node(loop_stmt, "in")
            if live is None:
                return None
            result = {self._get_simple_name(qn) for qn in live}
            result.update(n.value for n in m.findall(loop_stmt.iter, m.Name()))
            return result
        if isinstance(loop_stmt, cst.While):
            live = self._live_set_from_node(loop_stmt.test, "in")
            if live is None:
                return None
            return {self._get_simple_name(qn) for qn in live}
        return None

    def _live_out_at_loop(self, loop_stmt: cst.For | cst.While) -> set[str] | None:
        """
        Vars live just inside the loop body's start. Used as a sound (slightly loose)
        upper bound on what's live just after the loop exits, since the analysis joins
        body-needs and post-loop-needs at end-of-body.
        """
        node = loop_stmt if isinstance(loop_stmt, cst.For) else loop_stmt.test
        live = self._live_set_from_node(node, "out")
        if live is None:
            return None
        return {self._get_simple_name(qn) for qn in live}

    def _add_synthetic_loop_vars(self, vars_set: set[str]) -> None:
        """Always include synthetic loop index / comprehension result vars from defined_vars."""
        for v in self.defined_vars:
            if v.startswith(("__loop_index_", "_comp_result_")):
                vars_set.add(v)

    def _compute_vars_to_save(self, stmt: cst.CSTNode) -> set[str]:
        """
        Compute the set of variables that need to be saved at a split point.

        Uses live_out ∩ defined_vars when liveness data is available; otherwise
        falls back to all defined variables (sound but conservative).

        Always includes synthetic loop index / comprehension-result variables.
        """

        live_out = self._get_live_out(stmt)
        if live_out is not None:
            live_names = {self._get_simple_name(qn) for qn in live_out}
            vars_to_save = live_names & self.defined_vars
            self._add_synthetic_loop_vars(vars_to_save)
            return vars_to_save

        # Fallback: save all defined variables
        return set(self.defined_vars)

    @staticmethod
    def _collect_assigned_vars(stmts: list) -> set[str]:
        """
        Recursively collect all variable names assigned anywhere in the given
        statements (top level + inside any nested if/for/while).
        """
        result: set[str] = set()
        for stmt in stmts:
            if isinstance(stmt, cst.SimpleStatementLine):
                for element in stmt.body:
                    if isinstance(element, cst.Assign):
                        for target in element.targets:
                            if isinstance(target.target, cst.Name) and target.target.value != "__state__":
                                result.add(target.target.value)
                    elif isinstance(element, cst.AugAssign):
                        if isinstance(element.target, cst.Name) and element.target.value != "__state__":
                            result.add(element.target.value)
                    elif isinstance(element, cst.AnnAssign) and (
                        isinstance(element.target, cst.Name)
                        and element.value is not None
                        and element.target.value != "__state__"
                    ):
                        result.add(element.target.value)
            elif isinstance(stmt, cst.If):
                result.update(FunctionProcessor._collect_assigned_vars(list(stmt.body.body)))
                if stmt.orelse:
                    if isinstance(stmt.orelse, cst.Else):
                        result.update(FunctionProcessor._collect_assigned_vars(list(stmt.orelse.body.body)))
                    elif isinstance(stmt.orelse, cst.If):
                        result.update(FunctionProcessor._collect_assigned_vars([stmt.orelse]))
            elif isinstance(stmt, (cst.For, cst.While)):
                result.update(FunctionProcessor._collect_assigned_vars(list(stmt.body.body)))
                if isinstance(stmt, cst.For):
                    if isinstance(stmt.target, cst.Name):
                        result.add(stmt.target.value)
                    elif isinstance(stmt.target, cst.Tuple):
                        for elem in stmt.target.elements:
                            if isinstance(elem.value, cst.Name):
                                result.add(elem.value.value)
        return result

    def process(self) -> list[cst.FunctionDef]:
        """
        Main logic: Scans, Splits, and Returns a list of functions.
        """
        body = list(self.original_func.body.body)
        new_body = self._split_body(body)

        modified = self.original_func.with_changes(body=cst.IndentedBlock(body=new_body))
        # Sort by step number
        self.generated_functions.sort(key=lambda f: int(f.name.value.rsplit("_", 1)[-1]))
        return [modified, *self.generated_functions]

    def _split_body(self, body: list, loop_context=None) -> list:
        for i, stmt in enumerate(body):
            if self._is_send_async(stmt):
                body[i] = self._transform_send_async(stmt)
                continue

            # gather(...) → fan-out + barrier join
            if self._is_gather(stmt):
                return self._handle_gather(body, i, loop_context)

            # continue → jump back to loop step
            if self._is_continue(stmt) and loop_context:
                # TODO: FIX THIS TO WORK WITH LIVE VARS
                vars_to_save = loop_context.continue_save_vars & self.defined_vars
                self._add_synthetic_loop_vars(vars_to_save)
                direct = self._create_direct_continuation_call(loop_context.loop_step_name, vars_to_save)
                return [*body[:i], direct]

            # break → jump to post-loop continuation (or return if none)
            if self._is_break(stmt) and loop_context:
                if loop_context.post_loop_func_name and loop_context.break_save_vars is not None:
                    vars_to_save = loop_context.break_save_vars & self.defined_vars
                    self._add_synthetic_loop_vars(vars_to_save)
                    jump = self._create_direct_continuation_call(loop_context.post_loop_func_name, vars_to_save)
                else:
                    jump = cst.SimpleStatementLine(body=[cst.Return(value=None)])
                return [*body[:i], jump]

            if self._is_remote_call(stmt):
                return self._handle_remote_call(body, i, loop_context)

            if isinstance(stmt, cst.If) and self._if_contains_remote_call(stmt, loop_context):
                return self._handle_if(body, i, loop_context)

            if isinstance(stmt, cst.If):
                body[i] = self._transform_send_async_in_if(stmt)

            if isinstance(stmt, cst.For) and self._for_contains_remote_call(stmt):
                return self._handle_loop(body, i, loop_context)

            if isinstance(stmt, cst.For):
                body[i] = self._transform_send_async_in_compound(stmt)

            if isinstance(stmt, cst.While) and self._while_contains_remote_call(stmt):
                return self._handle_loop(body, i, loop_context)

            if isinstance(stmt, cst.While):
                body[i] = self._transform_send_async_in_compound(stmt)

            self._track_vars(stmt)

        # No remote calls found — tail of a loop body or normal function end
        if loop_context:
            if body and self._ends_with_raise(body):
                return body
            # Tail back-edge to loop step: pass live-in at loop header (same set
            # `continue` would pass), filtered by what's actually defined here.
            vars_to_save = loop_context.continue_save_vars & self.defined_vars
            self._add_synthetic_loop_vars(vars_to_save)
            direct_call = self._create_direct_continuation_call(loop_context.loop_step_name, vars_to_save)
            return [*body, direct_call]
        return body

    def _is_gather(self, stmt) -> bool:
        """Check if a statement is `<target> = gather(...)` or a bare `gather(...)`."""
        if not isinstance(stmt, cst.SimpleStatementLine) or not stmt.body:
            return False
        element = stmt.body[0]
        if isinstance(element, (cst.Assign, cst.Expr)):
            val = element.value
        else:
            return False
        return isinstance(val, cst.Call) and isinstance(val.func, cst.Name) and val.func.value == "gather"

    def _handle_gather(self, body: list, i: int, loop_context=None) -> list:
        """Split at a `gather(...)` call: fan-out N parallel dispatches into a join step."""
        stmt = body[i]
        post_split = body[i + 1 :]

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
            gather_call = element.value
        elif isinstance(element, cst.Expr):
            gather_call = element.value
        else:
            msg = f"Unsupported gather statement element: {type(element).__name__}"
            raise ValueError(msg)

        # Detect the dynamic spread case: `gather(*<comp>)` — a single starred
        # comprehension arg becomes a runtime fan-out, with arity = len(comp.iter).
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
        # mutations to self.defined_vars (adding gather targets) don't leak into
        # the dispatch block / restore block.
        vars_to_save = set(self._compute_vars_to_save(stmt))

        self.split_counter += 1
        join_name = f"{self.original_func.name.value}_step_{self.split_counter}"

        # Build dispatch BEFORE adding gather targets to defined_vars
        if spread_comp is not None:
            dispatch_block = self._create_gather_spread_dispatch_block(spread_comp, join_name, vars_to_save)
        else:
            dispatch_block = self._create_gather_dispatch_block(gather_args, join_name, vars_to_save)

        # The gather's targets become defined AFTER the join step resumes —
        # add them now so post-gather processing (inside join body) tracks them.
        if target_name is not None:
            self.defined_vars.add(target_name)
        if tuple_target is not None:
            for el in tuple_target.elements:
                if isinstance(el.value, cst.Name):
                    self.defined_vars.add(el.value.value)

        join_body = self._build_gather_join_body(target_name, tuple_target, vars_to_save, post_split, loop_context)
        join_func = self._create_continuation(join_name, join_body, "_gather_partial")
        self.generated_functions.append(join_func)

        return body[:i] + dispatch_block

    def _create_gather_dispatch_block(self, gather_args: list, join_name: str, vars_to_save: set | None) -> list:
        """Generate barrier init + N call_remote_async statements for a gather."""
        reply_op_name = self.entities[self.class_name]
        save_set = vars_to_save if vars_to_save is not None else self.defined_vars
        sorted_vars = sorted(self._get_simple_name(v) for v in save_set)
        saved_dict = context_dict(sorted_vars)

        statements: list = [init_gather_barrier_stmt(cst.Integer(str(len(gather_args))), saved_dict)]

        for tag, call_node in enumerate(gather_args):
            receiver, method = self._resolve_gather_arg_call(call_node, "gather()")
            if method == "insert" and isinstance(receiver, cst.Name) and receiver.value not in self.entities:
                msg = (
                    f"gather() argument calls must be entity methods or constructors, "
                    f"got plain function '{receiver.value}'"
                )
                raise ValueError(msg)
            target_op = self._resolve_operator_name(receiver)
            key_value = self._resolve_key_for_call(receiver, call_node, method)

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

    @staticmethod
    def _resolve_gather_arg_call(call_node, ctx_label: str) -> tuple[cst.BaseExpression, str]:
        """Validate a gather argument call and return (receiver, method).

        Constructor calls map to method 'insert'; method calls return the attr name.
        """
        if not isinstance(call_node, cst.Call):
            msg = f"{ctx_label} arguments must be method calls, got {type(call_node).__name__}"
            raise ValueError(msg)
        # NOTE: caller (FunctionProcessor) holds self.entities; this static helper
        # is only the structural shape check — entity-name validation stays inline.
        if isinstance(call_node.func, cst.Name):
            return call_node.func, "insert"
        if isinstance(call_node.func, cst.Attribute):
            return call_node.func.value, call_node.func.attr.value
        msg = f"Unsupported {ctx_label} argument call type: {type(call_node.func).__name__}"
        raise ValueError(msg)

    def _create_gather_spread_dispatch_block(
        self,
        comp: cst.ListComp | cst.SetComp | cst.GeneratorExp,
        join_name: str,
        vars_to_save: set | None,
    ) -> list:
        """Generate barrier init + a runtime for-loop of call_remote_async dispatches
        for `gather(*<comp>)`. Barrier arity is `len(comp.iter)`; each iteration emits
        one child call tagged by enumerate index.
        """
        if comp.for_in.inner_for_in is not None or comp.for_in.ifs:
            msg = "gather(*<comp>) only supports a single, unfiltered for-clause"
            raise ValueError(msg)

        call_node = comp.elt
        receiver, method = self._resolve_gather_arg_call(call_node, "gather(*<comp>)")
        if method == "insert" and isinstance(receiver, cst.Name) and receiver.value not in self.entities:
            msg = (
                f"gather(*<comp>) elements must be entity methods or constructors, "
                f"got plain function '{receiver.value}'"
            )
            raise ValueError(msg)

        target_op = self._resolve_operator_name(receiver)
        key_value = self._resolve_key_for_call(receiver, call_node, method)

        reply_op_name = self.entities[self.class_name]
        save_set = vars_to_save if vars_to_save is not None else self.defined_vars
        sorted_vars = sorted(self._get_simple_name(v) for v in save_set)
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
        # Tuple targets like `i, item` need explicit parens — otherwise
        # `_g_tag, i, item` flattens into a 3-tuple that doesn't match enumerate.
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

        return [materialize_stmt, init_stmt, dispatch_loop]

    def _build_gather_join_body(
        self,
        target_name: str | None,
        tuple_target: cst.Tuple | None,
        vars_to_save: set | None,
        post_split: list,
        loop_context,
    ) -> list:
        """Build the body of a gather join continuation."""
        save_set = vars_to_save if vars_to_save is not None else self.defined_vars
        sorted_vars = sorted(self._get_simple_name(v) for v in save_set)

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

        # Bind gather result to user's target.
        bind_target: cst.BaseExpression | None = tuple_target or (
            cst.Name(target_name) if target_name is not None else None
        )
        if bind_target is not None:
            body_stmts.append(assign_stmt(bind_target, cst.Name("_g_results")))

        # Recursively process post-gather body (may contain more remote calls / gathers).
        body_stmts.extend(self._split_body(list(post_split), loop_context))
        return body_stmts

    def _handle_remote_call(self, body: list, i: int, loop_context=None) -> list:
        """Split at a remote call found at index i in body."""
        stmt = body[i]
        post_split = body[i + 1 :]
        has_continuation = len(post_split) > 0

        target_var, call_node, receiver, remote_method, tuple_target = FunctionProcessor._extract_call_info(stmt)

        # Determine which variables to save at this split point
        vars_to_save = self._compute_vars_to_save(stmt)

        if has_continuation:
            self.split_counter += 1
            next_func_name = f"{self.original_func.name.value}_step_{self.split_counter}"

            dispatch_block = self._create_dispatch_block(
                receiver, remote_method, call_node, next_func_name, vars_to_save
            )

            restore_block = self._create_restore_block(vars_to_save)

            # Handle tuple unpacking: inject unpack statement and track individual vars
            unpack_stmts = []
            if tuple_target is not None:
                # Add each element of the tuple to defined_vars
                for el in tuple_target.elements:
                    if isinstance(el.value, cst.Name):
                        self.defined_vars.add(el.value.value)
                # Create unpacking statement: (a, b) = __tuple_result
                unpack_stmts.append(
                    cst.SimpleStatementLine(
                        body=[
                            cst.Assign(
                                targets=[cst.AssignTarget(target=tuple_target)],
                                value=cst.Name(target_var),
                            )
                        ]
                    )
                )
            elif target_var != "placeholder_return":
                self.defined_vars.add(target_var)

            cont_body = restore_block + unpack_stmts + self._split_body(post_split, loop_context)

            cont_func = self._create_continuation(next_func_name, cont_body, target_var)
            self.generated_functions.append(cont_func)

            return body[:i] + dispatch_block
        # Last remote call
        if loop_context:
            dispatch_block = self._create_dispatch_block(
                receiver, remote_method, call_node, loop_context.loop_step_name, vars_to_save
            )
        else:
            dispatch_block = self._create_dispatch_block(receiver, remote_method, call_node, "None", vars_to_save)

        return body[:i] + dispatch_block

    def _handle_if(self, body: list, i: int, loop_context=None) -> list:
        """Split at an if-statement (at index i) whose branches contain remote calls."""
        pre_if = body[:i]
        result = self._process_if_node(body[i], body[i + 1 :], loop_context)
        return pre_if + result

    def _process_if_node(self, if_stmt, post_if, loop_context=None):
        """Recursively process an if/elif node. Returns a list of statements."""
        if_body_stmts = list(if_stmt.body.body)
        if_branch_dispatches = self._any_remote_call(if_body_stmts, loop_context)

        # Snapshot before branching
        saved_vars = self.defined_vars.copy()

        # If a branch already ends in `return` or `raise`, post_if is dead code.
        if_tail = [] if self._ends_with_terminator(if_body_stmts) else post_if

        # Process if-branch
        new_if_body = self._split_body(if_body_stmts + if_tail, loop_context)

        # Restore for else/elif branch
        self.defined_vars = saved_vars.copy()

        # Process else/elif branch
        new_else = None
        if if_stmt.orelse is not None:
            if isinstance(if_stmt.orelse, cst.Else):
                else_body_stmts = list(if_stmt.orelse.body.body)
                else_tail = [] if self._ends_with_terminator(else_body_stmts) else post_if
                new_else_body = self._split_body(else_body_stmts + else_tail, loop_context)
                new_else = cst.Else(body=cst.IndentedBlock(body=new_else_body))
            elif isinstance(if_stmt.orelse, cst.If):
                # elif chain
                elif_result = self._process_if_node(if_stmt.orelse, post_if, loop_context)
                if len(elif_result) == 1 and isinstance(elif_result[0], cst.If):
                    new_else = elif_result[0]
                else:
                    new_else = cst.Else(body=cst.IndentedBlock(body=elif_result))
        elif if_branch_dispatches and post_if:
            # No else but if-branch dispatches — put post-if in else to prevent fallthrough
            processed_post_if = self._split_body(post_if, loop_context)
            new_else = cst.Else(body=cst.IndentedBlock(body=processed_post_if))
        elif if_branch_dispatches and not post_if and loop_context:
            # No else, no post-if, but inside a loop — the false path must loop back.
            vars_to_save = loop_context.continue_save_vars & self.defined_vars
            self._add_synthetic_loop_vars(vars_to_save)
            loop_back = self._create_direct_continuation_call(loop_context.loop_step_name, vars_to_save)
            new_else = cst.Else(body=cst.IndentedBlock(body=[loop_back]))

        # Restore to pre-branch state (caller determines what's defined after)
        self.defined_vars = saved_vars

        new_if = if_stmt.with_changes(body=cst.IndentedBlock(body=new_if_body), orelse=new_else)

        if if_stmt.orelse is not None or (if_branch_dispatches and (post_if or loop_context)):
            return [new_if]
        return [new_if, *post_if]

    def _parse_loop_iter(self, iter_node):
        """Returns (start_expr, bound_expr, is_range). Supports range() and collection iteration."""
        if isinstance(iter_node, cst.Call) and isinstance(iter_node.func, cst.Name) and iter_node.func.value == "range":
            if len(iter_node.args) == 1:
                return cst.Integer("0"), iter_node.args[0].value, True
            if len(iter_node.args) >= 2:
                return iter_node.args[0].value, iter_node.args[1].value, True

        # Collection iteration (for item in items)
        bound = cst.Call(func=cst.Name("len"), args=[cst.Arg(value=iter_node)])
        return cst.Integer("0"), bound, False

    def _handle_loop(self, body: list, i: int, loop_context=None) -> list:
        """
        Split at a for/while-loop whose body contains remote calls.
        """
        loop_stmt = body[i]
        pre_loop = body[:i]
        post_loop = body[i + 1 :]

        is_for = isinstance(loop_stmt, cst.For)
        op_name = self.entities[self.class_name]

        self.split_counter += 1
        loop_step_name = f"{self.original_func.name.value}_step_{self.split_counter}"

        # For-loop specific initialization
        init_iter = None
        var_assigns: list | None = None  # list of SimpleStatementLine (1 for normal, N for zip)
        inc_idx = None
        loop_var_name = "_loop_var"
        extra_loop_vars: list[str] = []  # additional vars to add to defined_vars (zip targets)
        bound_expr = None
        state_idx_access = None

        if is_for:
            self.loop_iter_counter += 1
            iter_var_name = f"__loop_index_{self.loop_iter_counter}"

            state_idx_access = cst.Name(iter_var_name)

            # Detect zip() iterator
            iter_node = loop_stmt.iter
            is_zip_iter = (
                isinstance(iter_node, cst.Call)
                and isinstance(iter_node.func, cst.Name)
                and iter_node.func.value == "zip"
            )

            if is_zip_iter:
                # for a, b in zip(xs, ys):
                # Bound = len of first zip argument; access via xs[i], ys[i].
                zip_args = [arg.value for arg in iter_node.args]
                bound_expr = cst.Call(func=cst.Name("len"), args=[cst.Arg(value=zip_args[0])])

                init_iter = assign_stmt(cst.Name(iter_var_name), cst.Integer("0"))
                self.defined_vars.add(iter_var_name)

                # Resolve tuple target names: for (a, b) in zip(…) or for a in zip(…)
                target = loop_stmt.target
                if isinstance(target, cst.Tuple):
                    target_names = [elem.value.value for elem in target.elements if isinstance(elem.value, cst.Name)]
                elif isinstance(target, cst.Name):
                    target_names = [target.value]
                else:
                    target_names = []

                # Generate: a = xs[__loop_index]; b = ys[__loop_index]; …
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
                extra_loop_vars = target_names[1:]  # remaining tuple elements

            else:
                start_expr, bound_expr, is_range = self._parse_loop_iter(iter_node)

                init_iter = assign_stmt(cst.Name(iter_var_name), start_expr)
                self.defined_vars.add(iter_var_name)

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

            inc_idx = cst.SimpleStatementLine(
                body=[cst.AugAssign(target=state_idx_access, operator=cst.AddAssign(), value=cst.Integer("1"))]
            )

        # Pre-scan loop body for variable assignments. These are vars that may flow
        # into the loop step via the body's back-edge (in addition to anything from
        # outside the loop).
        loop_body_stmts = list(loop_stmt.body.body)
        loop_body_vars = self._collect_assigned_vars(loop_body_stmts)

        # Universe of names known to exist somewhere in/around the loop. Used to
        # filter live sets down to vars we can actually serialize.
        known_vars = set(self.defined_vars) | loop_body_vars

        # Live-in at loop entry: what's needed to (re-)evaluate the iter/test and
        # body. Used for both initial entry and the loop step's restore. Falls back
        # to all known vars if liveness data is unavailable.
        live_in = self._live_in_at_loop(loop_stmt) if isinstance(loop_stmt, (cst.For, cst.While)) else None
        if live_in is not None:
            entry_save_vars = live_in & known_vars
            self._add_synthetic_loop_vars(entry_save_vars)
        else:
            entry_save_vars = set(known_vars)

        # Live-out of the loop: an upper bound on what the post-loop code needs.
        live_out = self._live_out_at_loop(loop_stmt) if isinstance(loop_stmt, (cst.For, cst.While)) else None
        if live_out is not None:
            post_loop_save_vars = live_out & known_vars
            self._add_synthetic_loop_vars(post_loop_save_vars)
        else:
            post_loop_save_vars = set(known_vars)

        # Initial entry into the loop: only the outside knows about pre-loop vars.
        vars_for_loop_entry = entry_save_vars & self.defined_vars
        self._add_synthetic_loop_vars(vars_for_loop_entry)
        direct_call = self._create_direct_continuation_call(loop_step_name, vars_for_loop_entry)

        # Restore at the start of the loop step covers everything any incoming edge
        # (initial entry or body back-edge) might pass.
        restore_block = self._create_restore_block(entry_save_vars)

        # Snapshot before processing branches
        saved_vars = self.defined_vars.copy()

        # Post-loop code: processed with the OUTER loop_context (not this loop's)
        if post_loop:
            self.split_counter += 1
            post_loop_func_name = f"{self.original_func.name.value}_step_{self.split_counter}"
            post_loop_body = self._split_body(post_loop, loop_context)
            post_loop_restore = self._create_restore_block(post_loop_save_vars)
            post_loop_func = self._create_continuation(
                post_loop_func_name, post_loop_restore + post_loop_body, "placeholder_return"
            )
            self.generated_functions.append(post_loop_func)
            exit_stmts = [self._create_direct_continuation_call(post_loop_func_name, post_loop_save_vars)]
        else:
            post_loop_func_name = None
            exit_stmts = [cst.SimpleStatementLine(body=[cst.Return(value=None)])]

        # Restore before processing loop body (independent path)
        self.defined_vars = saved_vars.copy()

        if is_for and loop_var_name != "_loop_var":
            self.defined_vars.add(loop_var_name)
        for v in extra_loop_vars:  # additional zip tuple-target variables
            self.defined_vars.add(v)

        # Process loop body in loop mode
        inner_loop_context = LoopContext(
            loop_step_name=loop_step_name,
            op_name=op_name,
            iter_var_name=state_idx_access.value if is_for else None,
            post_loop_func_name=post_loop_func_name,
            continue_save_vars=entry_save_vars,
            break_save_vars=post_loop_save_vars if post_loop else None,
        )
        loop_body_processed = self._split_body(loop_body_stmts, inner_loop_context)

        # Restore to pre-branch state
        self.defined_vars = saved_vars

        if is_for:
            # Use if/else structure for bounds checking
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
                # No if/else wrapper needed; emit the body statements directly.
                loop_step_body = [*restore_block, *loop_body_processed]
            else:
                # Use if/else structure for condition checking
                body_block = cst.IndentedBlock(body=loop_body_processed)
                else_block = cst.Else(body=cst.IndentedBlock(body=exit_stmts))
                if_block = cst.If(test=test_cond, body=body_block, orelse=else_block)
                loop_step_body = [*restore_block, if_block]

        cont_func = self._create_continuation(loop_step_name, loop_step_body, "placeholder_return")
        self.generated_functions.append(cont_func)

        if is_for:
            return [*pre_loop, init_iter, direct_call]
        return [*pre_loop, direct_call]

    def _create_direct_continuation_call(self, func_name: str, vars_to_save: set | None = None):
        """
        Create a call_remote_async to a continuation on the same entity,
        without building a reply_to entry.
        """
        op_name = self.entities[self.class_name]

        save_set = vars_to_save if vars_to_save is not None else self.defined_vars
        sorted_vars = sorted(self._get_simple_name(v) for v in save_set)
        ctx = context_dict(sorted_vars)

        params = continuation_params(ctx, cst.Name("None"), cst.Name("reply_to"))
        return call_remote_async_stmt(op_name, func_name, CTX_KEY, params)

    def _any_remote_call(self, stmts: list, loop_context=None) -> bool:
        """Recursively check if any statement in the list contains a remote call."""
        for stmt in stmts:
            if self._is_remote_call(stmt) or self._is_gather(stmt):
                return True
            if loop_context and (self._is_continue(stmt) or self._is_break(stmt)):
                return True
            if isinstance(stmt, cst.If):
                if self._any_remote_call(list(stmt.body.body), loop_context):
                    return True
                if stmt.orelse is not None and (
                    (
                        isinstance(stmt.orelse, cst.Else)
                        and self._any_remote_call(list(stmt.orelse.body.body), loop_context)
                    )
                    or (isinstance(stmt.orelse, cst.If) and self._any_remote_call([stmt.orelse], loop_context))
                ):
                    return True
            if isinstance(stmt, cst.For) and self._for_contains_remote_call(stmt):
                return True
            if isinstance(stmt, cst.While) and self._while_contains_remote_call(stmt):
                return True
        return False

    def _if_contains_remote_call(self, node: cst.If, loop_context=None) -> bool:
        """Check if any branch of an if-statement contains remote calls."""
        if self._any_remote_call(list(node.body.body), loop_context):
            return True
        if node.orelse is not None:
            if isinstance(node.orelse, cst.Else):
                if self._any_remote_call(list(node.orelse.body.body), loop_context):
                    return True
            elif isinstance(node.orelse, cst.If) and self._if_contains_remote_call(node.orelse, loop_context):
                return True
        return False

    def _for_contains_remote_call(self, node: cst.For) -> bool:
        """Check if a For loop's body contains remote calls."""
        return self._any_remote_call(list(node.body.body))

    def _while_contains_remote_call(self, node: cst.While) -> bool:
        """Check if a While loop's body contains remote calls."""
        return self._any_remote_call(list(node.body.body))

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_call_info(stmt):
        element = stmt.body[0]
        tuple_target = None
        if isinstance(element, cst.Assign):
            target = element.targets[0].target
            if isinstance(target, cst.Tuple):
                # Tuple unpacking: price, stock = self.ret_tuple(item)
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

    def _is_continue(self, stmt) -> bool:
        return isinstance(stmt, cst.SimpleStatementLine) and bool(stmt.body) and isinstance(stmt.body[0], cst.Continue)

    def _is_break(self, stmt) -> bool:
        return isinstance(stmt, cst.SimpleStatementLine) and bool(stmt.body) and isinstance(stmt.body[0], cst.Break)

    def _track_vars(self, stmt):
        """Add any variables assigned (recursively) within stmt to self.defined_vars."""
        self.defined_vars.update(self._collect_assigned_vars([stmt]))

    def _ends_with_raise(self, body: list) -> bool:
        """Check if the last statement is a raise (making any following code unreachable)."""
        if not body:
            return False
        last = body[-1]
        if isinstance(last, cst.SimpleStatementLine):
            return any(isinstance(el, cst.Raise) for el in last.body)
        return False

    def _ends_with_terminator(self, body: list) -> bool:
        """Last statement is a return or raise — any following code is unreachable."""
        if not body:
            return False
        last = body[-1]
        if isinstance(last, cst.SimpleStatementLine):
            return any(isinstance(el, (cst.Return, cst.Raise)) for el in last.body)
        return False

    def _transform_send_async_in_if(self, node: cst.If) -> cst.If:
        """Recursively transform send_async calls inside if/elif/else blocks."""
        new_body = self._transform_send_async_in_block(list(node.body.body))
        new_if = node.with_changes(body=cst.IndentedBlock(body=new_body))
        if node.orelse is not None:
            if isinstance(node.orelse, cst.Else):
                new_else_body = self._transform_send_async_in_block(list(node.orelse.body.body))
                new_else = node.orelse.with_changes(body=cst.IndentedBlock(body=new_else_body))
                new_if = new_if.with_changes(orelse=new_else)
            elif isinstance(node.orelse, cst.If):
                new_if = new_if.with_changes(orelse=self._transform_send_async_in_if(node.orelse))
        return new_if

    def _transform_send_async_in_block(self, body: list) -> list:
        """Transform send_async calls in a block, recursing into all compound statements."""
        for i, stmt in enumerate(body):
            if self._is_send_async(stmt):
                body[i] = self._transform_send_async(stmt)
            elif isinstance(stmt, cst.If):
                body[i] = self._transform_send_async_in_if(stmt)
            elif isinstance(stmt, (cst.For, cst.While)):
                body[i] = self._transform_send_async_in_compound(stmt)
        return body

    def _transform_send_async_in_compound(self, node):
        """Transform send_async calls inside a for/while loop body (and else clause)."""
        new_body = self._transform_send_async_in_block(list(node.body.body))
        new_node = node.with_changes(body=cst.IndentedBlock(body=new_body))
        if node.orelse is not None and isinstance(node.orelse, cst.Else):
            new_else_body = self._transform_send_async_in_block(list(node.orelse.body.body))
            new_else = node.orelse.with_changes(body=cst.IndentedBlock(body=new_else_body))
            new_node = new_node.with_changes(orelse=new_else)
        return new_node

    def _is_send_async(self, stmt):
        """Check if a statement is send_async(remote_call)."""
        if not isinstance(stmt, cst.SimpleStatementLine) or not stmt.body:
            return False
        element = stmt.body[0]
        if not isinstance(element, cst.Expr) or not isinstance(element.value, cst.Call):
            return False
        func = element.value.func
        return isinstance(func, cst.Name) and func.value == "send_async"

    def _transform_send_async(self, stmt):
        """Rewrite `send_async(<remote_call>)` to a fire-and-forget ctx.call_remote_async.

        The reply_to is rooted with a sink marker so any return value bubbling up
        through this chain is swallowed by send_reply, never leaking into a real
        caller's reply stream.
        """
        inner_call = stmt.body[0].value.args[0].value  # send_async(<inner>) → <inner>

        if isinstance(inner_call.func, cst.Attribute):
            receiver = inner_call.func.value
            method = inner_call.func.attr.value
        elif isinstance(inner_call.func, cst.Name):
            receiver = inner_call.func
            method = "insert"
        else:
            msg = f"Unsupported call type inside send_async: {type(inner_call.func)}"
            raise ValueError(msg)

        key_value = self._resolve_key_for_call(receiver, inner_call, method)
        op_name = self._resolve_operator_name(receiver)

        original_args = [arg.value for arg in inner_call.args]
        params_value = params_with_reply_to(original_args, sink_reply_to())
        return call_remote_async_stmt(op_name, method, key_value, params_value)

    def _is_remote_call(self, stmt):
        if not isinstance(stmt, cst.SimpleStatementLine) or not stmt.body:
            return False

        element = stmt.body[0]
        if isinstance(element, (cst.Assign, cst.Expr)):
            val = element.value
        else:
            return False

        if not isinstance(val, cst.Call):
            return False

        # Ignore self.__key__()
        if (
            isinstance(val.func, cst.Attribute)
            and isinstance(val.func.value, cst.Name)
            and val.func.value.value == "self"
            and val.func.attr.value == "__key__"
        ):
            return False

        # Case 1: entity initialization, item = Item(name, price)
        if isinstance(val.func, cst.Name):
            return val.func.value in self.entities

        # Case 2: Attribute calls (obj.method() or self.obj.method())
        # Use mypy metadata to check if the receiver evaluates to an entity type
        if isinstance(val.func, cst.Attribute):
            receiver = val.func.value
            return self._is_entity_node(receiver)

        return False

    def _is_entity_node(self, node) -> bool:
        """Check if a CST node's mypy type resolves to an entity."""
        return self._get_entity_type(node) is not None

    def _get_entity_type(self, node) -> str | None:
        mypy_type = self.metadata.get(node)

        if mypy_type is not None:
            # Only check the outermost type - e.g. list[Item] gives 'list', not 'Item'
            # This prevents list.append() / list.sort() from being treated as entity calls
            type_name = self._extract_outermost_type_name(mypy_type)
            if type_name in self.entities:
                return type_name

        if isinstance(node, cst.Call) and isinstance(node.func, cst.Name) and node.func.value in self.entities:
            return node.func.value

        # Fallback for newly generated AST nodes (e.g. from transform_init) where metadata is None
        if isinstance(node, cst.Name):
            for param in getattr(self.original_func.params, "params", []):
                if param.name.value == node.value and param.annotation:
                    ann = param.annotation.annotation
                    if isinstance(ann, cst.Name) and ann.value in self.entities:
                        return ann.value

        return None

    def _extract_outermost_type_name(self, mypy_type) -> str:
        """Extract the outermost class name, ignoring generics.
        e.g. 'builtins.list[module.Item]' -> 'list'
             'test_tmp.Item' -> 'Item'
             'test_tmp.Item | None' -> 'Item'  (Optional unwrapping)
        """
        fullname = mypy_type.fullname

        # Handle Optional / Union types: 'module.Item | None' -> 'module.Item'
        # Optional[X] is represented by mypy as 'X | None'
        if " | " in fullname:
            parts = [p.strip() for p in fullname.split(" | ") if p.strip() != "None"]
            if len(parts) == 1:
                fullname = parts[0]

        # Drop generic part: 'builtins.list[module.Item]' -> 'builtins.list'
        if "[" in fullname:
            fullname = fullname.split("[")[0]
        return fullname.rsplit(".", 1)[-1]

    def _resolve_operator_name(self, receiver: cst.BaseExpression) -> str:
        # Case: Item() -> receiver is cst.Name(value="Item"), entity constructor
        if isinstance(receiver, cst.Name) and receiver.value in self.entities:
            return self.entities[receiver.value]

        # Use mypy metadata to resolve the receiver's type
        type_name = self._get_entity_type(receiver)
        if type_name:
            return self.entities[type_name]

        return self.entities.get(self.class_name, "unknown_operator")

    def _resolve_key_for_call(self, receiver, call_node, method):
        """
        Resolve the correct key= argument for a remote call.
        For constructor calls (method='insert'), build the key expression from attributes.
        For method calls, the receiver variable IS the key.
        """
        if method == "insert" and isinstance(receiver, cst.Name):
            entity_class = receiver.value
            key_attrs = self.entity_keys.get(entity_class)
            init_params = self.entity_init_params.get(entity_class)

            if key_attrs and init_params:
                param_names = list(init_params.keys())
                resolved_parts = []
                for attr in key_attrs:
                    if attr in param_names:
                        idx = param_names.index(attr)
                        if idx < len(call_node.args):
                            arg_val = call_node.args[idx].value
                            # Multi-key case: wrap everything in str() and concatenate with ":"
                            if len(key_attrs) > 1:
                                resolved_parts.append(cst.Call(func=cst.Name("str"), args=[cst.Arg(value=arg_val)]))
                            else:
                                # Single key case:
                                return arg_val

                if len(resolved_parts) > 1:
                    # Join with + ":" +
                    expr = resolved_parts[0]
                    for part in resolved_parts[1:]:
                        expr = cst.BinaryOperation(
                            left=cst.BinaryOperation(left=expr, operator=cst.Add(), right=cst.SimpleString('":"')),
                            operator=cst.Add(),
                            right=part,
                        )
                    return expr

        # Fallback: use the receiver itself (works for method calls like item.get_price())
        return receiver

    def _create_dispatch_block(self, receiver, method, call_node, next_func_name, vars_to_save: set | None = None):
        key_value = self._resolve_key_for_call(receiver, call_node, method)

        original_args = [arg.value for arg in call_node.args]
        params_value = params_with_reply_to(original_args, cst.Name("reply_to"))

        op_name = self._resolve_operator_name(receiver)
        async_call = call_remote_async_stmt(op_name, method, key_value, params_value)

        if next_func_name == "None":
            return [async_call]

        save_set = vars_to_save if vars_to_save is not None else self.defined_vars
        sorted_vars = sorted(self._get_simple_name(v) for v in save_set)
        ctx = context_dict(sorted_vars)

        reply_op_name = self.entities[self.class_name]
        push_call = push_continuation_stmt(reply_op_name, next_func_name, ctx)

        return [push_call, async_call]

    def _create_restore_block(self, vars_to_restore: set | None = None):
        """Restore locals from func_context via resolve_context + tuple-unpack."""
        vars_source = vars_to_restore if vars_to_restore is not None else self.defined_vars
        sorted_vars = sorted(self._get_simple_name(v) for v in vars_source)

        if not sorted_vars:
            return []

        return [resolve_context_stmt(), restore_tuple_from("params", sorted_vars)]

    def _create_continuation(self, name, body, target_var):
        """Create a continuation function (decorated, async) on this class's operator."""
        return continuation_func(name, body, target_var, self.entities[self.class_name])
