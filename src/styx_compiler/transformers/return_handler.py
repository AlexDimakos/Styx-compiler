"""Wrap user `return` statements in `send_reply(...)` and inject `ctx.put(__state__)` before every dispatch/return."""

import libcst as cst


class ReturnHandlerTransformer(cst.CSTTransformer):
    """
    Finds 'return' statements and wraps them with the reply_to stack logic.
    Also injects 'ctx.put(state)' immediately before the logic.
    """

    def __init__(self, uses_state: bool):
        super().__init__()

        # If we don't use state at all no need to inject ctx.put(__state__)
        self.uses_state = uses_state

        # True while inside a gather-join step. Bare `return` inside such steps is basically a yield
        # and should not be rewritten to send_reply.
        self._in_gather_join = False

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        if any(p.name.value == "_gather_partial" for p in node.params.params):
            self._in_gather_join = True
        return True

    def _is_graph_terminal(self, node: cst.CSTNode | None) -> bool:
        """
        Recursively checks if a node guarantees an exit (return, raise, or async dispatch).
        """
        if node is None:
            return False

        if isinstance(node, (cst.Return, cst.Raise, cst.Break, cst.Continue)):
            return True

        if isinstance(node, cst.SimpleStatementLine):
            # Check for ctx.call_remote_async(...) — after dispatch, function is done
            for child in node.body:
                if isinstance(child, cst.Expr) and isinstance(child.value, cst.Call):
                    func = child.value.func
                    if (
                        isinstance(func, cst.Attribute)
                        and isinstance(func.value, cst.Name)
                        and func.value.value == "ctx"
                        and func.attr.value == "call_remote_async"
                    ):
                        return True
            return any(self._is_graph_terminal(child) for child in node.body)

        if isinstance(node, cst.IndentedBlock):
            if not node.body:
                return False
            return self._is_graph_terminal(node.body[-1])

        if isinstance(node, cst.If):
            if node.orelse is None:
                return False

            body_terminal = self._is_graph_terminal(node.body)

            else_terminal = False
            if isinstance(node.orelse, cst.Else):
                else_terminal = self._is_graph_terminal(node.orelse.body)
            elif isinstance(node.orelse, cst.If):  # elif chain
                else_terminal = self._is_graph_terminal(node.orelse)

            return body_terminal and else_terminal

        return False

    def _is_call_remote_async(self, node: cst.CSTNode) -> bool:
        """Check if a statement is a ctx.call_remote_async(...) call."""
        if isinstance(node, cst.SimpleStatementLine):
            for el in node.body:
                if isinstance(el, cst.Expr) and isinstance(el.value, cst.Call):
                    func = el.value.func
                    if (
                        isinstance(func, cst.Attribute)
                        and isinstance(func.value, cst.Name)
                        and func.value.value == "ctx"
                        and func.attr.value == "call_remote_async"
                    ):
                        return True
        return False

    def _has_reply_to_param(self, node: cst.FunctionDef) -> bool:
        """Check if a function has a reply_to parameter."""
        return any(param.name.value == "reply_to" for param in node.params.params)

    def leave_SimpleStatementLine(self, _original_node, updated_node):
        # Handle ctx.call_remote_async: prepend ctx.put(__state__) if uses_state
        if self._is_call_remote_async(updated_node) and self.uses_state:
            put_state = cst.parse_statement("ctx.put(__state__)")
            return cst.FlattenSentinel([put_state, updated_node])

        return_node = None
        for node in updated_node.body:
            if isinstance(node, cst.Return):
                return_node = node
                break

        if not return_node:
            return updated_node

        # This means we have not yet received all the replies so we should not send a reply yet.
        if self._in_gather_join and return_node.value is None:
            return updated_node

        ret_val = return_node.value if return_node.value else cst.Name("None")

        # Ensure implicit tuples (return a, b) get parenthesized so they
        # become a single argument: send_reply(ctx, reply_to, (a, b))
        if isinstance(ret_val, cst.Tuple) and not ret_val.lpar:
            ret_val = ret_val.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()])

        # Generate: return send_reply(ctx, reply_to, result)
        send_reply_call = cst.Call(
            func=cst.Name("send_reply"),
            args=[
                cst.Arg(value=cst.Name("ctx")),
                cst.Arg(value=cst.Name("reply_to")),
                cst.Arg(value=ret_val),
            ],
        )

        res_stmt = cst.SimpleStatementLine(body=[cst.Return(value=send_reply_call)])

        put_state = cst.parse_statement("ctx.put(__state__)")

        return cst.FlattenSentinel([put_state, res_stmt]) if self.uses_state else res_stmt

    def leave_FunctionDef(self, _original_node, updated_node):
        body_stmts = updated_node.body.body
        last_stmt = body_stmts[-1] if body_stmts else None

        if not self._is_graph_terminal(last_stmt):
            new_body = list(updated_node.body.body)
            # Add send_reply for functions with reply_to so the reply chain isn't lost
            if self._has_reply_to_param(updated_node):
                if self.uses_state:
                    new_body.append(cst.parse_statement("ctx.put(__state__)"))
                if not self._in_gather_join:
                    new_body.append(cst.parse_statement("return send_reply(ctx, reply_to, None)"))
            elif self.uses_state:
                # No reply_to but state used update state
                new_body.append(cst.parse_statement("ctx.put(__state__)"))

            if new_body != list(updated_node.body.body):
                return updated_node.with_changes(body=updated_node.body.with_changes(body=new_body))

        return updated_node
