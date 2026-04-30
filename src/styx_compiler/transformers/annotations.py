"""Rewrite type annotations that reference entities to their key types, e.g. `item: Item` -> `item: str`."""

import libcst as cst


class AnnotationNameReplacer(cst.CSTTransformer):
    def __init__(self, get_key_type_func):
        super().__init__()
        self.get_key_type = get_key_type_func

    def leave_Name(self, original_node, updated_node):
        replacement = self.get_key_type(original_node.value)
        if replacement:
            return updated_node.with_changes(value=replacement)
        return updated_node


class EntityTypeReplacer(cst.CSTTransformer):
    """
    Replaces entity type references in annotations with the key's type.
    e.g., `item: Item` -> `item: str`, `-> Item` -> `-> str`
    Also handles: `items: list[Item]` -> `items: list[str]`, `list[list[Item]]` -> `list[list[str]]`
    """

    def __init__(
        self,
        entity_keys: dict[str, str],
        entity_init_params: dict[str, dict[str, str]],
        entity_key_types: dict[str, str] | None = None,
    ):
        super().__init__()
        self.entity_keys = entity_keys
        self.entity_init_params = entity_init_params
        self.entity_key_types = entity_key_types or {}

    def _get_key_type(self, entity_name: str):
        """Resolve an entity name to its key's type string, or None."""
        # 1. Check if we explicitly found a return type for __key__
        if entity_name in self.entity_key_types:
            return self.entity_key_types[entity_name]

        # 2. Check if the key field is in __init__ params
        key_fields = self.entity_keys.get(entity_name)
        init_params = self.entity_init_params.get(entity_name)

        if key_fields and isinstance(key_fields, list):
            if len(key_fields) > 1:
                # Composite keys are concatenated into strings
                return "str"

            # Single key: use its original type
            key_field = key_fields[0]
            if init_params and key_field in init_params:
                return init_params[key_field]

        # 3. Fallback: if it's a known entity, default to str
        if entity_name in self.entity_keys:
            return "str"

        return None

    def leave_Annotation(self, _original_node, updated_node):
        replacer = AnnotationNameReplacer(self._get_key_type)
        new_ann = updated_node.annotation.visit(replacer)
        return updated_node.with_changes(annotation=new_ann)
