"""Rewrite boolean expressions containing remote calls into explicit ifs to preserve short circuit semantics."""

import libcst as cst


def _has_extractable_call(node: cst.CSTNode, entities: dict[str, str], in_send_async: bool = False) -> bool:
    """Does this subtree contain a call that the extractor would hoist?

    Mirrors CallExtractorAndReplacer's extraction rules: entity constructors and
    any attribute-style call count; send_async, gather, and self.__key__() do not.
    """
    if isinstance(node, cst.Call):
        if isinstance(node.func, cst.Name) and node.func.value in ("send_async", "gather"):
            return False
        if in_send_async:
            return False
        if (
            isinstance(node.func, cst.Attribute)
            and isinstance(node.func.value, cst.Name)
            and node.func.value.value == "self"
            and node.func.attr.value == "__key__"
        ):
            return False
        if isinstance(node.func, cst.Name) and node.func.value in entities:
            return True
        if isinstance(node.func, cst.Attribute):
            return True

    return any(_has_extractable_call(child, entities, in_send_async) for child in node.children)


class ShortCircuitRewriter(cst.CSTTransformer):
    """
    Rewrite short-circuiting boolean operations that would be hoisted by linearizer
    into if-statements, so that we preserve short-circuit semantics.

    Example:

        if a.get_stock() > 0 and a.get_price() < 100:
            body

    becomes:

        _sc_1 = a.get_stock() > 0
        if _sc_1:
            _sc_1 = a.get_price() < 100
        if _sc_1:
            body

    These will then be hoisted by the linearzer, but the short-circuit semantics are preserved by the nested ifs.
    """

    def __init__(self, entities: dict[str, str] | None = None):
        super().__init__()
        self.entities = entities or {}
        self.current_class: str | None = None
        self._counter_stack: list[int] = [0]

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self.current_class = node.name.value
        return True

    def leave_ClassDef(self, _original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        self.current_class = None
        return updated_node

    def visit_FunctionDef(self, _node: cst.FunctionDef) -> bool:
        self._counter_stack.append(0)
        return True

    def leave_FunctionDef(self, _original_node, updated_node):
        self._counter_stack.pop()
        return updated_node

    def _next_var(self) -> str:
        self._counter_stack[-1] += 1
        return f"_sc_{self._counter_stack[-1]}"

    def _rewrite_boolop(self, expr: cst.BaseExpression) -> tuple[list[cst.BaseStatement], cst.BaseExpression]:
        """Rewrite a BooleanOperation containing extractable calls.

        Returns (hoisted_statements, new_expression). If expr is not a
        BooleanOperation or doesn't contain any extractable call, returns
        ([], expr) unchanged.
        """
        if not isinstance(expr, cst.BooleanOperation):
            return [], expr
        if not _has_extractable_call(expr, self.entities):
            return [], expr

        left_hoist, left_expr = self._rewrite_boolop(expr.left)
        right_hoist, right_expr = self._rewrite_boolop(expr.right)

        sc_var = self._next_var()

        init_stmt = cst.SimpleStatementLine(
            body=[
                cst.Assign(
                    targets=[cst.AssignTarget(target=cst.Name(sc_var))],
                    value=left_expr,
                )
            ]
        )

        # `a and b` → compute b only if _sc is truthy
        # `a or  b` → compute b only if _sc is falsy
        if isinstance(expr.operator, cst.Or):
            inner_test: cst.BaseExpression = cst.UnaryOperation(
                operator=cst.Not(whitespace_after=cst.SimpleWhitespace(" ")),
                expression=cst.Name(sc_var),
            )
        else:
            inner_test = cst.Name(sc_var)

        inner_body_stmts = [
            *right_hoist,
            cst.SimpleStatementLine(
                body=[
                    cst.Assign(
                        targets=[cst.AssignTarget(target=cst.Name(sc_var))],
                        value=right_expr,
                    )
                ]
            ),
        ]

        if_block = cst.If(
            test=inner_test,
            body=cst.IndentedBlock(body=inner_body_stmts),
            orelse=None,
        )

        return [*left_hoist, init_stmt, if_block], cst.Name(sc_var)

    def _rewrite_in_simple_stmt(self, stmt: cst.SimpleStatementLine) -> list[cst.BaseStatement]:
        """Rewrite top-level BoolOps in each small-statement's primary expression."""
        all_hoisted: list[cst.BaseStatement] = []
        new_body: list[cst.BaseSmallStatement] = []
        changed = False

        for small in stmt.body:
            if (
                isinstance(small, cst.Assign)
                or (isinstance(small, cst.AnnAssign) and small.value is not None)
                or (isinstance(small, cst.Return) and small.value is not None)
                or isinstance(small, cst.Expr)
            ):
                hoisted, new_value = self._rewrite_boolop(small.value)
                if hoisted:
                    all_hoisted.extend(hoisted)
                    new_body.append(small.with_changes(value=new_value))
                    changed = True
                else:
                    new_body.append(small)
            else:
                new_body.append(small)

        if changed:
            return [*all_hoisted, stmt.with_changes(body=new_body)]
        return [stmt]

    def _rewrite_if_chain(self, if_stmt: cst.If) -> list[cst.BaseStatement]:
        """Rewrite BoolOps in an if/elif chain's tests.

        Elif chains are flattened into nested if/else so hoisted statements can
        live inside the preceding else branch.
        """
        hoisted, new_test = self._rewrite_boolop(if_stmt.test)

        # Recurse into the else/elif.
        new_orelse: cst.Else | cst.If | None = if_stmt.orelse
        if isinstance(if_stmt.orelse, cst.If):
            # Elif: rewrite it as a list of statements.
            inner = self._rewrite_if_chain(if_stmt.orelse)
            if len(inner) == 1 and isinstance(inner[0], cst.If):
                new_orelse = inner[0]
            else:
                new_orelse = cst.Else(body=cst.IndentedBlock(body=inner))
        # Else blocks and nested statements inside them are already handled by
        # leave_IndentedBlock recursively

        new_if = if_stmt.with_changes(test=new_test, orelse=new_orelse)

        if hoisted:
            return [*hoisted, new_if]
        return [new_if]

    def _rewrite_while(self, while_stmt: cst.While) -> list[cst.BaseStatement]:
        """Rewrite BoolOps in a while-loop test.

        Since the test must re-evaluate each iteration, the hoisted statements
        are pushed into the body and the loop becomes `while True` with an
        explicit break guard. The later RemoteCallLinearizer.leave_While
        normalization is happy with this shape.
        """
        hoisted, new_test = self._rewrite_boolop(while_stmt.test)
        if not hoisted:
            return [while_stmt]

        parenthesized = (
            new_test
            if isinstance(new_test, cst.Name)
            else new_test.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()])
        )
        break_if = cst.If(
            test=cst.UnaryOperation(
                operator=cst.Not(whitespace_after=cst.SimpleWhitespace(" ")),
                expression=parenthesized,
            ),
            body=cst.IndentedBlock(body=[cst.SimpleStatementLine(body=[cst.Break()])]),
            orelse=None,
        )
        new_body_stmts = [*hoisted, break_if, *list(while_stmt.body.body)]
        return [
            while_stmt.with_changes(
                test=cst.Name("True"),
                body=while_stmt.body.with_changes(body=new_body_stmts),
            )
        ]

    def _rewrite_statement(self, stmt: cst.BaseStatement) -> list[cst.BaseStatement]:
        if isinstance(stmt, cst.If):
            return self._rewrite_if_chain(stmt)
        if isinstance(stmt, cst.While):
            return self._rewrite_while(stmt)
        if isinstance(stmt, cst.SimpleStatementLine):
            return self._rewrite_in_simple_stmt(stmt)
        return [stmt]

    def leave_IndentedBlock(
        self, _original_node: cst.IndentedBlock, updated_node: cst.IndentedBlock
    ) -> cst.IndentedBlock:
        if self.current_class is not None and self.current_class not in self.entities:
            return updated_node

        new_body: list[cst.BaseStatement] = []
        for stmt in updated_node.body:
            new_body.extend(self._rewrite_statement(stmt))
        return updated_node.with_changes(body=new_body)
