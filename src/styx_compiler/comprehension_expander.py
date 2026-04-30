"""
Comprehension-to-loop expander.
"""

import libcst as cst
import libcst.matchers as m


def find_gather_spread_comps(node: cst.CSTNode) -> set[int]:
    """Return ids of comprehensions used as the spread arg of `gather(*<comp>)`.

    Those must stay intact so the processor can fan them out in parallel rather
    than serializing them into an accumulator + for-loop.
    """
    skip: set[int] = set()
    for call in m.findall(node, m.Call(func=m.Name("gather"))):
        for arg in call.args:
            if arg.star == "*" and isinstance(arg.value, (cst.ListComp, cst.SetComp, cst.GeneratorExp)):
                skip.add(id(arg.value))
    return skip


class OutermostCompFinder(cst.CSTVisitor):
    """Finds the first (outermost) comprehension in a node to preserve nested scoping."""

    def __init__(self, skip_ids: set[int] | None = None) -> None:
        self.target: cst.CSTNode | None = None
        self._skip_ids = skip_ids or set()

    def _check_and_stop(self, node: cst.CSTNode) -> bool:
        # Skipped comps (e.g. `gather(*<comp>)`) keep descending so inner comps still get found.
        if id(node) in self._skip_ids:
            return True
        if self.target is None:
            self.target = node
        return False  # Always stop traversing children so we only get the outermost

    def visit_ListComp(self, node: cst.ListComp) -> bool:
        return self._check_and_stop(node)

    def visit_SetComp(self, node: cst.SetComp) -> bool:
        return self._check_and_stop(node)

    def visit_DictComp(self, node: cst.DictComp) -> bool:
        return self._check_and_stop(node)

    def visit_GeneratorExp(self, node: cst.GeneratorExp) -> bool:
        return self._check_and_stop(node)

    def visit_FunctionDef(self, _node: cst.FunctionDef) -> bool:
        return False

    def visit_ClassDef(self, _node: cst.ClassDef) -> bool:
        return False


class TargetedReplacer(cst.CSTTransformer):
    """Replaces only the specifically targeted comprehension node and builds its loop."""

    def __init__(self, target: cst.CSTNode, var_name: str) -> None:
        super().__init__()
        self.target = target
        self.var_name = var_name
        self.hoisted: list[cst.BaseStatement] = []

    # Skip children of the target so inner comprehensions stay intact for the next pass
    def visit_ListComp(self, node: cst.ListComp) -> bool:
        return node is not self.target

    def visit_SetComp(self, node: cst.SetComp) -> bool:
        return node is not self.target

    def visit_DictComp(self, node: cst.DictComp) -> bool:
        return node is not self.target

    def visit_GeneratorExp(self, node: cst.GeneratorExp) -> bool:
        return node is not self.target

    def visit_FunctionDef(self, _node: cst.FunctionDef) -> bool:
        return False

    def visit_ClassDef(self, _node: cst.ClassDef) -> bool:
        return False

    def leave_ListComp(self, original_node: cst.ListComp, updated_node: cst.ListComp) -> cst.BaseExpression:
        if original_node is self.target:
            self.hoisted.extend(_build_list_comp_loop(self.var_name, original_node))
            return cst.Name(self.var_name)
        return updated_node

    def leave_SetComp(self, original_node: cst.SetComp, updated_node: cst.SetComp) -> cst.BaseExpression:
        if original_node is self.target:
            self.hoisted.extend(_build_set_comp_loop(self.var_name, original_node))
            return cst.Name(self.var_name)
        return updated_node

    def leave_DictComp(self, original_node: cst.DictComp, updated_node: cst.DictComp) -> cst.BaseExpression:
        if original_node is self.target:
            self.hoisted.extend(_build_dict_comp_loop(self.var_name, original_node))
            return cst.Name(self.var_name)
        return updated_node

    def leave_GeneratorExp(self, original_node: cst.GeneratorExp, updated_node: cst.GeneratorExp) -> cst.BaseExpression:
        if original_node is self.target:
            self.hoisted.extend(_build_list_comp_loop(self.var_name, original_node))
            return cst.Name(self.var_name)
        return updated_node


class ComprehensionExpander(cst.CSTTransformer):
    def __init__(self, entities: dict[str, str] | None = None) -> None:
        super().__init__()
        self.entities = entities or {}
        self.current_class: str | None = None
        self._counter = 0

        # Stack to handle nested functions (visit happens twice before leave)
        self._counter_stack: list[int] = []

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self.current_class = node.name.value
        return True

    def leave_ClassDef(self, _original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        self.current_class = None
        return updated_node

    def visit_FunctionDef(self, _node: cst.FunctionDef) -> bool:
        self._counter_stack.append(self._counter)
        self._counter = 0
        return True

    def leave_FunctionDef(self, _original_node, updated_node):
        self._counter = self._counter_stack.pop()
        return updated_node

    def _next_var(self) -> str:
        self._counter += 1
        return f"_comp_result_{self._counter}"

    def leave_IndentedBlock(
        self, _original_node: cst.IndentedBlock, updated_node: cst.IndentedBlock
    ) -> cst.IndentedBlock:
        return self._process_block(updated_node)

    def leave_Module(self, _original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        return self._process_block(updated_node)

    def _process_block(self, node: cst.IndentedBlock | cst.Module) -> cst.IndentedBlock | cst.Module:
        # Only expand if we are NOT in a class OR if we are in an entity class
        if self.current_class is not None and self.current_class not in self.entities:
            return node

        modified = True
        current_body = list(node.body)

        # Keep processing the block until all comprehensions are flattened out.
        while modified:
            modified = False
            new_body = []

            for stmt in current_body:
                # `gather(*<comp>)` keeps its comp intact for runtime fan-out.
                skip_ids = find_gather_spread_comps(stmt)
                finder = OutermostCompFinder(skip_ids=skip_ids)
                stmt.visit(finder)

                if finder.target:
                    var = self._next_var()
                    replacer = TargetedReplacer(finder.target, var)

                    # 1. Replace the target in the current statement
                    new_stmt = stmt.visit(replacer)

                    # 2. Recursively visit the newly generated loop statements
                    # otherwise [[item.get_stock() for item in items] for _ in range(3)] wouldn't work
                    processed_hoisted = [h.visit(self) for h in replacer.hoisted]

                    # 3. Append the properly scoped statements
                    new_body.extend(processed_hoisted)
                    new_body.append(new_stmt)

                    modified = True
                else:
                    new_body.append(stmt)

            current_body = new_body

        return node.with_changes(body=current_body)


def _wrap_in_ifs(body: list[cst.BaseStatement], ifs: tuple[cst.CompIf, ...]) -> list[cst.BaseStatement]:
    result = body
    for comp_if in reversed(ifs):
        result = [cst.If(test=comp_if.test, body=cst.IndentedBlock(body=result), leading_lines=[])]
    return result


def _build_for_chain(for_in: cst.CompFor, innermost_body: list[cst.BaseStatement]) -> cst.For:
    if for_in.inner_for_in is not None:
        inner_loop = _build_for_chain(for_in.inner_for_in, innermost_body)
        body_with_ifs = _wrap_in_ifs([inner_loop], for_in.ifs)
    else:
        body_with_ifs = _wrap_in_ifs(innermost_body, for_in.ifs)

    is_async = for_in.asynchronous

    return cst.For(
        target=for_in.target, iter=for_in.iter, body=cst.IndentedBlock(body=body_with_ifs), asynchronous=is_async
    )


def _build_list_comp_loop(var: str, node: cst.ListComp | cst.GeneratorExp) -> list[cst.BaseStatement]:
    init = cst.parse_statement(f"{var} = []")
    append = cst.SimpleStatementLine(
        body=[
            cst.Expr(
                value=cst.Call(
                    func=cst.Attribute(value=cst.Name(var), attr=cst.Name("append")),
                    args=[cst.Arg(value=node.elt)],
                )
            )
        ]
    )
    return [init, _build_for_chain(node.for_in, [append])]


def _build_set_comp_loop(var: str, node: cst.SetComp) -> list[cst.BaseStatement]:
    init = cst.parse_statement(f"{var} = set()")
    add = cst.SimpleStatementLine(
        body=[
            cst.Expr(
                value=cst.Call(
                    func=cst.Attribute(value=cst.Name(var), attr=cst.Name("add")),
                    args=[cst.Arg(value=node.elt)],
                )
            )
        ]
    )
    return [init, _build_for_chain(node.for_in, [add])]


def _build_dict_comp_loop(var: str, node: cst.DictComp) -> list[cst.BaseStatement]:
    init = cst.parse_statement(f"{var} = {{}}")
    assign = cst.SimpleStatementLine(
        body=[
            cst.Assign(
                targets=[
                    cst.AssignTarget(
                        target=cst.Subscript(
                            value=cst.Name(var),
                            slice=[cst.SubscriptElement(slice=cst.Index(value=node.key))],
                        )
                    )
                ],
                value=node.value,
            )
        ]
    )
    return [init, _build_for_chain(node.for_in, [assign])]
