"""Pre-pass that rewrites every `send_async(<remote_call>)` statement in a
function body into a fire-and-forget `ctx.call_remote_async(...)` with a
a sink entry pushed in the reply_to stack indicating that returns should be supressed.
"""

import libcst as cst

from styx_compiler.cst_helpers import (
    call_remote_async_stmt,
    params_with_reply_to,
    sink_reply_to,
)
from styx_compiler.entity_resolver import EntityResolver


def is_send_async_stmt(stmt: cst.CSTNode) -> bool:
    if not isinstance(stmt, cst.SimpleStatementLine) or not stmt.body:
        return False
    element = stmt.body[0]
    if not isinstance(element, cst.Expr) or not isinstance(element.value, cst.Call):
        return False
    func = element.value.func
    return isinstance(func, cst.Name) and func.value == "send_async"


def rewrite_send_async_in_function(func: cst.FunctionDef, resolver: EntityResolver) -> cst.FunctionDef:
    """Return `func` with all send_async(...) statements rewritten."""
    rewriter = _Rewriter(resolver)
    new_body = rewriter.rewrite_block(list(func.body.body))
    if not rewriter.touched:
        return func
    return func.with_changes(body=cst.IndentedBlock(body=new_body))


class _Rewriter:
    """In-place walker that replaces send_async(<remote_call>) with ctx.call_remote_async(...)"""

    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver
        self.touched = False

    def rewrite_block(self, body: list) -> list:
        for i, stmt in enumerate(body):
            if is_send_async_stmt(stmt):
                body[i] = self._rewrite_stmt(stmt)
                self.touched = True
            elif isinstance(stmt, cst.If):
                rebuilt = self._rewrite_if(stmt)
                if rebuilt is not stmt:
                    body[i] = rebuilt
            elif isinstance(stmt, (cst.For, cst.While)):
                rebuilt = self._rewrite_loop(stmt)
                if rebuilt is not stmt:
                    body[i] = rebuilt
        return body

    def _rewrite_if(self, node: cst.If) -> cst.If:
        before = self.touched
        self.touched = False
        new_body = self.rewrite_block(list(node.body.body))
        body_touched = self.touched

        new_orelse = node.orelse
        orelse_touched = False
        if node.orelse is not None:
            if isinstance(node.orelse, cst.Else):
                self.touched = False
                new_else_body = self.rewrite_block(list(node.orelse.body.body))
                if self.touched:
                    new_orelse = node.orelse.with_changes(body=cst.IndentedBlock(body=new_else_body))
                    orelse_touched = True
            elif isinstance(node.orelse, cst.If):
                rebuilt = self._rewrite_if(node.orelse)
                if rebuilt is not node.orelse:
                    new_orelse = rebuilt
                    orelse_touched = True

        self.touched = before or body_touched or orelse_touched
        if not (body_touched or orelse_touched):
            return node
        return node.with_changes(
            body=cst.IndentedBlock(body=new_body),
            orelse=new_orelse,
        )

    def _rewrite_loop(self, node):
        before = self.touched
        self.touched = False
        new_body = self.rewrite_block(list(node.body.body))
        body_touched = self.touched

        new_orelse = node.orelse
        orelse_touched = False
        if node.orelse is not None and isinstance(node.orelse, cst.Else):
            self.touched = False
            new_else_body = self.rewrite_block(list(node.orelse.body.body))
            if self.touched:
                new_orelse = node.orelse.with_changes(body=cst.IndentedBlock(body=new_else_body))
                orelse_touched = True

        self.touched = before or body_touched or orelse_touched
        if not (body_touched or orelse_touched):
            return node
        return node.with_changes(
            body=cst.IndentedBlock(body=new_body),
            orelse=new_orelse,
        )

    def _rewrite_stmt(self, stmt: cst.SimpleStatementLine) -> cst.SimpleStatementLine:
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

        key_value = self.resolver.key_for_call(receiver, inner_call, method)
        op_name = self.resolver.operator_name_for(receiver)

        original_args = [arg.value for arg in inner_call.args]
        params_value = params_with_reply_to(original_args, sink_reply_to())
        return call_remote_async_stmt(op_name, method, key_value, params_value)
