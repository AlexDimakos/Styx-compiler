"""Rewrite `self.attr`, `self`, `self.__key__()`, `get_entity_by_key`, and `exists()`."""

import libcst as cst
import libcst.matchers as m


class StateAccessTransformer(cst.CSTTransformer):
    """
    Transforms:
    1. self.attribute     -> __state__['attribute']
    2. self               -> ctx.key
    3. self.__key__()     -> ctx.key
    4. get_entity_by_key(Entity, key) -> key
    5. exists(self)       -> bool(__state__)
    """

    def __init__(self, metadata=None, entity_keys=None, entity_init_params=None):
        super().__init__()
        self.metadata = metadata or {}
        self.entity_keys = entity_keys or {}
        self.entity_init_params = entity_init_params or {}

    def _get_node_type(self, node):
        """Helper to get the type name from mypy metadata or CST node type (for literals)."""
        mypy_type = self.metadata.get(node)
        if mypy_type:
            # Simple extraction for the print statement
            fullname = mypy_type.fullname
            type_name = fullname.rsplit(".", 1)[-1]

            # Handle union types like "int | None"
            if "|" in type_name:
                type_name = type_name.split("|")[0].strip()
            return type_name

        # Fallback for literals
        if isinstance(node, cst.SimpleString):
            return "str"
        if isinstance(node, cst.Integer):
            return "int"
        if isinstance(node, cst.Float):
            return "float"
        if isinstance(node, cst.Name) and node.value in ("True", "False"):
            return "bool"

        return "None"

    def leave_Call(self, original_node, updated_node):
        # Handles self.__key__() -> ctx.key
        if m.matches(original_node, m.Call(func=m.Attribute(value=m.Name("self"), attr=m.Name("__key__")))):
            return cst.Attribute(value=cst.Name("ctx"), attr=cst.Name("key"))

        # Handles exists(self) -> bool(__state__). Currently only supports exists(self), maybe could be extended?
        if m.matches(original_node, m.Call(func=m.Name("exists"))):
            if len(original_node.args) != 1 or not m.matches(original_node.args[0].value, m.Name("self")):
                msg = "exists() currently only supports a single argument 'self'"
                raise ValueError(msg)
            return cst.Call(func=cst.Name("bool"), args=[cst.Arg(value=cst.Name("__state__"))])

        # Handles get_entity_by_key(Entity, key) -> key (or concatenated string for tuples)
        if m.matches(original_node, m.Call(func=m.Name("get_entity_by_key"))) and len(updated_node.args) >= 2:
            entity_node = updated_node.args[0].value
            key = updated_node.args[1].value

            if isinstance(entity_node, cst.Name):
                entity_name = entity_node.value
                key_attrs = self.entity_keys.get(entity_name, [])
                init_params = self.entity_init_params.get(entity_name, {})
                expected_types = [init_params.get(a) for a in key_attrs]

                # 1. Structure validation (only possible for literal tuple keys)
                if len(key_attrs) > 1 and isinstance(key, cst.Tuple) and len(key.elements) != len(key_attrs):
                    msg = (
                        f"get_entity_by_key for {entity_name} expects "
                        f"{len(key_attrs)} elements, but got {len(key.elements)}"
                    )
                    raise TypeError(msg)

                # 2. Collect types for comparison and reporting
                if isinstance(key, cst.Tuple):
                    actual_types = []
                    original_tuple = original_node.args[1].value
                    for i in range(len(key.elements)):
                        original_element = original_tuple.elements[i].value
                        actual_types.append(self._get_node_type(original_element))

                    # Check for mismatches (skip if actual type could not be resolved)
                    for _i, (actual, expected) in enumerate(zip(actual_types, expected_types, strict=True)):
                        if actual not in (None, "None") and actual != expected:
                            expected_str = f"({', '.join(expected_types)})"
                            actual_str = f"({', '.join(actual_types)})"
                            msg = (
                                f"Type mismatch for retrieving '{entity_name}' by key: "
                                f"expected {expected_str}, got {actual_str}"
                            )
                            raise TypeError(msg)

                    # Build: ctx.key = f"{key[0]}:{key[1]}"
                    parts = []

                    for i, el in enumerate(key.elements):
                        if i > 0:
                            parts.append(cst.FormattedStringText(":"))

                        parts.append(cst.FormattedStringExpression(expression=el.value))

                    return cst.FormattedString(parts=parts)
                original_key = original_node.args[1].value
                actual_type = self._get_node_type(original_key)
                expected_type = expected_types[0] if expected_types else None
                if actual_type not in (None, "None") and (expected_type != "None") and actual_type != expected_type:
                    msg = (
                        f"Type mismatch for retrieving '{entity_name}' by key: "
                        f"expected {expected_type}, got {actual_type}"
                    )
                    raise TypeError(msg)

            return updated_node.args[1].value

        return updated_node

    def leave_AnnAssign(self, _original_node, updated_node):
        # Python does not support __state__['x']: int = 5, so we need to remove the annotation
        # for things that are transformed to state access (e.g. self.x: int = 5 -> __state__['x'] = 5).
        if isinstance(updated_node.target, cst.Subscript):
            value = updated_node.value if updated_node.value is not None else cst.Name("None")
            return cst.Assign(targets=[cst.AssignTarget(target=updated_node.target)], value=value)
        return updated_node

    def leave_Attribute(self, original_node, updated_node):
        # Handles self.attribute -> __state__['attribute']
        if m.matches(original_node, m.Attribute(value=m.Name("self"))):
            return cst.Subscript(
                value=cst.Name("__state__"),
                slice=[cst.SubscriptElement(slice=cst.Index(value=cst.SimpleString(f"'{original_node.attr.value}'")))],
            )
        return updated_node

    def leave_Name(self, original_node, updated_node):
        # Handles standalone 'self' -> 'ctx.key'
        if m.matches(original_node, m.Name("self")):
            return cst.Attribute(value=cst.Name("ctx"), attr=cst.Name("key"))
        return updated_node
