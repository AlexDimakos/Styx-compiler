"""Hoist nested calls into one-call-per-statement form (ANF like pass)"""

import libcst as cst

from styx_compiler.transformers.normalize import normalize_function_body, normalize_inline_if


class RemoteCallLinearizer(cst.CSTTransformer):
    """
    Linearizes remote calls within functions.
    """

    def __init__(self, entities: dict[str, str] | None = None):
        self.entities = entities or {}
        self.call_counter = 0
        self.current_class: str | None = None

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self.current_class = node.name.value
        return True

    def leave_ClassDef(self, _original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        self.current_class = None
        return updated_node

    def leave_FunctionDef(self, _original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        """Process each function and linearize remote calls."""
        if self.current_class is not None and self.current_class not in self.entities:
            return updated_node

        # Normalize inline function bodies
        updated_node = normalize_function_body(updated_node)

        self.call_counter = 0

        linearizer = StatementLinearizer(self.entities)
        new_body = updated_node.body.visit(linearizer)

        return updated_node.with_changes(body=new_body)


class StatementLinearizer(cst.CSTTransformer):
    """
    Linearize method calls within statements.
    """

    def __init__(self, entities: dict[str, str] | None = None):
        self.entities = entities or {}
        self.counter = 1

    def leave_SimpleStatementLine(
        self, _original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine | cst.FlattenSentinel[cst.SimpleStatementLine]:
        """Process each statement and extract method calls."""
        new_statements = []

        for stmt in updated_node.body:
            extractor = CallExtractorAndReplacer(self.entities, self.counter)
            new_stmt = stmt.visit(extractor)

            should_collapse = False
            last_extracted_var = None
            last_extracted_call = None

            if extractor.extracted_calls:
                last_extracted_var, last_extracted_call = extractor.extracted_calls[-1]

                if isinstance(new_stmt, cst.Expr) and isinstance(new_stmt.value, cst.Name):
                    if new_stmt.value.value == last_extracted_var:
                        should_collapse = True

                elif (
                    isinstance(new_stmt, cst.Assign)
                    and len(new_stmt.targets) == 1
                    and isinstance(new_stmt.targets[0].target, cst.Name)
                    and isinstance(new_stmt.value, cst.Name)
                    and new_stmt.value.value == last_extracted_var
                ):
                    should_collapse = True

            if should_collapse:
                extractor.extracted_calls.pop()
                new_stmt = new_stmt.with_changes(value=last_extracted_call)

            self.counter = extractor.counter

            for var_name, call in extractor.extracted_calls:
                assignment = cst.SimpleStatementLine(
                    body=[cst.Assign(targets=[cst.AssignTarget(target=cst.Name(var_name))], value=call)]
                )
                new_statements.append(assignment)

            new_statements.append(cst.SimpleStatementLine(body=[new_stmt]))

        return cst.FlattenSentinel(new_statements)

    def leave_If(self, _original_node: cst.If, updated_node: cst.If) -> cst.If | cst.FlattenSentinel[cst.BaseStatement]:
        """Handle if statements specially."""
        # Normalize inline if bodies (SimpleStatementSuite -> IndentedBlock)
        updated_node = normalize_inline_if(updated_node)

        new_statements = []

        extractor = CallExtractorAndReplacer(self.entities, self.counter)
        new_test = updated_node.test.visit(extractor)
        self.counter = extractor.counter

        # If no calls were extracted, return unchanged to avoid
        # FlattenSentinel in positions that don't support it (e.g. elif)
        if not extractor.extracted_calls:
            return updated_node

        for var_name, call in extractor.extracted_calls:
            assignment = cst.SimpleStatementLine(
                body=[cst.Assign(targets=[cst.AssignTarget(target=cst.Name(var_name))], value=call)]
            )
            new_statements.append(assignment)

        new_if = updated_node.with_changes(test=new_test)
        new_statements.append(new_if)

        return cst.FlattenSentinel(new_statements)

    def leave_While(
        self, _original_node: cst.While, updated_node: cst.While
    ) -> cst.While | cst.FlattenSentinel[cst.BaseStatement]:
        """Handle while statements specially, extracting from test condition.

        When the condition containsa function call, transform:
            while <condition_using_remote_call>:
                <body>
        into:
            while True:
                <extracted call assignments>
                if not (<condition>):
                    break
                <body>

        This ensures the remote call result is re-fetched each iteration and the
        condition is checked against the fresh value, not a stale saved variable.
        """
        extractor = CallExtractorAndReplacer(self.entities, self.counter)
        new_test = updated_node.test.visit(extractor)
        self.counter = extractor.counter

        if not extractor.extracted_calls:
            return updated_node

        # Build the extracted call assignments — these go at the top of the body
        extracted_assignments = []
        for var_name, call in extractor.extracted_calls:
            assignment = cst.SimpleStatementLine(
                body=[cst.Assign(targets=[cst.AssignTarget(target=cst.Name(var_name))], value=call)]
            )
            extracted_assignments.append(assignment)

        # Build: if not (<condition>): break
        # Wrap new_test in parens to ensure correct precedence under `not`
        parenthesized_test = new_test.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()])
        not_test = cst.UnaryOperation(
            operator=cst.Not(whitespace_after=cst.SimpleWhitespace(" ")),
            expression=parenthesized_test,
        )
        break_if = cst.If(
            test=not_test,
            body=cst.IndentedBlock(body=[cst.SimpleStatementLine(body=[cst.Break()])]),
            orelse=None,
        )

        # New body: extracted assignments + guard + original body (no end-of-body re-extractions)
        new_body_stmts = [*extracted_assignments, break_if, *updated_node.body.body]
        new_body = updated_node.body.with_changes(body=new_body_stmts)

        # Replace loop condition with True — the guard above handles termination
        return updated_node.with_changes(test=cst.Name("True"), body=new_body)

    def leave_For(
        self, _original_node: cst.For, updated_node: cst.For
    ) -> cst.For | cst.FlattenSentinel[cst.BaseStatement]:
        """Handle for statements specially, extracting from iter condition."""
        new_statements = []

        extractor = CallExtractorAndReplacer(self.entities, self.counter)
        new_iter = updated_node.iter.visit(extractor)
        self.counter = extractor.counter

        if not extractor.extracted_calls:
            return updated_node

        for var_name, call in extractor.extracted_calls:
            assignment = cst.SimpleStatementLine(
                body=[cst.Assign(targets=[cst.AssignTarget(target=cst.Name(var_name))], value=call)]
            )
            new_statements.append(assignment)

        new_for = updated_node.with_changes(iter=new_iter)
        new_statements.append(new_for)

        return cst.FlattenSentinel(new_statements)


class CallExtractorAndReplacer(cst.CSTTransformer):
    """
    Extract and replace method calls with variables.
    """

    def __init__(self, entities: dict[str, str] | None = None, start_counter=1):
        self.entities = entities or {}
        self.extracted_calls: list[tuple[str, cst.BaseExpression]] = []
        self.counter = start_counter
        self._in_send_async = False
        self._in_gather = False

    def visit_Call(self, node: cst.Call) -> bool:
        """Set flag when entering send_async()/gather() to prevent extraction of inner calls."""
        if isinstance(node.func, cst.Name) and node.func.value == "send_async":
            self._in_send_async = True
        elif isinstance(node.func, cst.Name) and node.func.value == "gather":
            self._in_gather = True
        return True

    def leave_Call(self, _original_node: cst.Call, updated_node: cst.Call) -> cst.BaseExpression:
        # Don't extract send_async itself — clear flag and return unchanged

        if isinstance(updated_node.func, cst.Name) and updated_node.func.value == "send_async":
            self._in_send_async = False
            return updated_node

        # Don't extract gather itself — used for fan-out
        # all its inner calls in parallel via a barrier-and-join step.
        if isinstance(updated_node.func, cst.Name) and updated_node.func.value == "gather":
            self._in_gather = False
            return updated_node

        # Don't extract calls inside send_async or gather
        if self._in_send_async or self._in_gather:
            return updated_node

        # Don't extract self.__key__()
        if (
            isinstance(updated_node.func, cst.Attribute)
            and isinstance(updated_node.func.value, cst.Name)
            and updated_node.func.value.value == "self"
            and updated_node.func.attr.value == "__key__"
        ):
            return updated_node

        is_entity_instantiation = False
        if isinstance(updated_node.func, cst.Name) and updated_node.func.value in self.entities:
            is_entity_instantiation = True

        if is_entity_instantiation:
            receiver_var_name = f"attr_{self.counter}"
            self.counter += 1
            self.extracted_calls.append((receiver_var_name, updated_node))
            return cst.Name(receiver_var_name)

        if isinstance(updated_node.func, cst.Attribute):
            receiver = updated_node.func.value
            new_func = updated_node.func

            # Don't extract simple names or self.attribute as receivers
            is_simple_receiver = False
            if isinstance(receiver, cst.Name) or (
                isinstance(receiver, cst.Attribute)
                and isinstance(receiver.value, cst.Name)
                and receiver.value.value == "self"
            ):
                is_simple_receiver = True

            if not is_simple_receiver:
                receiver_var_name = f"attr_{self.counter}"
                self.counter += 1
                self.extracted_calls.append((receiver_var_name, receiver))

                new_func = new_func.with_changes(value=cst.Name(receiver_var_name))

            new_call = updated_node.with_changes(func=new_func)

            var_name = f"attr_{self.counter}"
            self.counter += 1

            self.extracted_calls.append((var_name, new_call))

            return cst.Name(var_name)

        return updated_node
