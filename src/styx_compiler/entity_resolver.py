"""Type and key resolution for remote call detection and routing in the Styx splitter."""

from collections.abc import Mapping

import libcst as cst


class EntityResolver:
    def __init__(
        self,
        class_name: str,
        original_func: cst.FunctionDef,
        entities: dict[str, str],
        metadata: Mapping,
        entity_keys: dict[str, list[str]] | None = None,
        entity_init_params: dict[str, list[str]] | None = None,
    ):
        self.class_name = class_name
        self.original_func = original_func
        self.entities = entities
        self.metadata = metadata
        self.entity_keys = entity_keys or {}
        self.entity_init_params = entity_init_params or {}

    # ── entity-type lookup ────────────────────────────────────────────

    def get_entity_type(self, node: cst.CSTNode) -> str | None:
        """Return the entity class name for a node, or None if it's not an entity."""
        mypy_type = self.metadata.get(node)
        if mypy_type is not None:
            type_name = self._extract_outermost_type_name(mypy_type)
            if type_name in self.entities:
                return type_name

        if isinstance(node, cst.Call) and isinstance(node.func, cst.Name) and node.func.value in self.entities:
            return node.func.value

        # libcst_mypy doesn't attach types to Subscript nodes, so derive the
        # element type from the collection's parameterized type
        # (e.g. i_ids: list[Item] -> Item).
        if isinstance(node, cst.Subscript):
            collection_type = self.metadata.get(node.value)
            if collection_type is not None:
                element_type = self._extract_element_type_name(collection_type)
                if element_type in self.entities:
                    return element_type

        if isinstance(node, cst.Name):
            return self._local_entity_binding(node.value)

        return None

    def _local_entity_binding(self, var_name: str) -> str | None:
        """Walk this function's body looking for `var_name = <rhs>` and return
        the entity class name implied by the rhs, or None."""
        for stmt in self.original_func.body.body:
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue
            for el in stmt.body:
                target = None
                rhs: cst.BaseExpression | None = None
                if isinstance(el, cst.Assign) and len(el.targets) == 1:
                    target = el.targets[0].target
                    rhs = el.value
                elif isinstance(el, cst.AnnAssign):
                    target = el.target
                    rhs = el.value
                if not (isinstance(target, cst.Name) and target.value == var_name and rhs is not None):
                    continue

                if isinstance(rhs, cst.Call) and isinstance(rhs.func, cst.Name) and rhs.func.value in self.entities:
                    return rhs.func.value

                if (
                    isinstance(rhs, cst.Call)
                    and isinstance(rhs.func, cst.Name)
                    and rhs.func.value == "get_entity_by_key"
                    and len(rhs.args) >= 1
                    and isinstance(rhs.args[0].value, cst.Name)
                    and rhs.args[0].value.value in self.entities
                ):
                    return rhs.args[0].value.value
        return None

    def is_entity_node(self, node: cst.CSTNode) -> bool:
        return self.get_entity_type(node) is not None

    @staticmethod
    def _extract_outermost_type_name(mypy_type) -> str:
        """Outermost class name, ignoring generics and unwrapping `X | None`.

        e.g. 'builtins.list[module.Item]' -> 'list'
             'test_tmp.Item' -> 'Item'
             'test_tmp.Item | None' -> 'Item'
        """
        fullname = mypy_type.fullname
        # Optional[X] is represented by mypy as 'X | None'.
        if " | " in fullname:
            parts = [p.strip() for p in fullname.split(" | ") if p.strip() != "None"]
            if len(parts) == 1:
                fullname = parts[0]
        if "[" in fullname:
            fullname = fullname.split("[")[0]
        return fullname.rsplit(".", 1)[-1]

    @staticmethod
    def _extract_element_type_name(mypy_type) -> str | None:
        """Element type name from a parameterized list/tuple/dict, or None."""

        fullname = getattr(mypy_type, "fullname", None)
        if not isinstance(fullname, str) or "[" not in fullname or not fullname.endswith("]"):
            return None
        container = fullname.split("[", 1)[0].rsplit(".", 1)[-1]
        inner = fullname[fullname.index("[") + 1 : -1]

        parts = [p.strip() for p in inner.split(",")]
        target = parts[-1] if container == "dict" and len(parts) >= 2 else parts[0]
        if target in ("...", "Any", ""):
            return None
        if "[" in target:
            target = target.split("[", 1)[0]
        return target.rsplit(".", 1)[-1]

    # ── operator + key resolution ─────────────────────────────────────

    def operator_name_for(self, receiver: cst.BaseExpression) -> str:
        """Operator routing target for a method/constructor receiver."""
        if isinstance(receiver, cst.Name) and receiver.value in self.entities:
            return self.entities[receiver.value]
        type_name = self.get_entity_type(receiver)
        if type_name:
            return self.entities[type_name]
        return self.entities.get(self.class_name, "unknown_operator")

    def key_for_call(self, receiver: cst.BaseExpression, call_node: cst.Call, method: str) -> cst.BaseExpression:
        """Get the key argument for a remote call.

        For new object instantiations we need to construct it using the constructor arguments and
        the def __key__() function which points to which ones they are.
        """
        if method == "insert" and isinstance(receiver, cst.Name):
            built = self._build_constructor_key(receiver.value, call_node)
            if built is not None:
                return built
        return receiver

    def _build_constructor_key(self, entity_class: str, call_node: cst.Call) -> cst.BaseExpression | None:
        key_attrs = self.entity_keys.get(entity_class)
        init_params = self.entity_init_params.get(entity_class)
        if not (key_attrs and init_params):
            return None

        param_names = list(init_params.keys())
        resolved_parts: list[cst.BaseExpression] = []
        for attr in key_attrs:
            if attr not in param_names:
                continue
            idx = param_names.index(attr)
            if idx >= len(call_node.args):
                continue
            arg_val = call_node.args[idx].value
            if len(key_attrs) == 1:
                return arg_val
            resolved_parts.append(cst.Call(func=cst.Name("str"), args=[cst.Arg(value=arg_val)]))

        if len(resolved_parts) <= 1:
            return None

        # Composite key: str(a) + ":" + str(b) + ":" + str(c) ...
        expr: cst.BaseExpression = resolved_parts[0]
        for part in resolved_parts[1:]:
            expr = cst.BinaryOperation(
                left=cst.BinaryOperation(left=expr, operator=cst.Add(), right=cst.SimpleString('":"')),
                operator=cst.Add(),
                right=part,
            )
        return expr

    # ── remote-call detection ─────────────────────────────────────────

    def is_remote_call(self, stmt: cst.CSTNode) -> bool:
        if not isinstance(stmt, cst.SimpleStatementLine) or not stmt.body:
            return False
        element = stmt.body[0]
        if not isinstance(element, (cst.Assign, cst.Expr)):
            return False
        val = element.value
        if not isinstance(val, cst.Call):
            return False

        # Ignore self.__key__() we later just replace it with ctx.key
        if (
            isinstance(val.func, cst.Attribute)
            and isinstance(val.func.value, cst.Name)
            and val.func.value.value == "self"
            and val.func.attr.value == "__key__"
        ):
            return False

        # Constructor call: Item(...)
        if isinstance(val.func, cst.Name):
            return val.func.value in self.entities

        # Method call: item.get_price()
        if isinstance(val.func, cst.Attribute):
            return self.is_entity_node(val.func.value)

        return False
