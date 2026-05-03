"""Slices an `@entity` method into chained continuation step functions."""

from collections.abc import Mapping

import libcst as cst

from styx_compiler.entity_resolver import EntityResolver
from styx_compiler.liveness import LivenessHelper
from styx_compiler.send_async import rewrite_send_async_in_function
from styx_compiler.splitting import LoopContext, SplitContext

__all__ = ["FunctionProcessor", "LoopContext"]


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
        resolver = EntityResolver(
            class_name=class_name,
            original_func=original_func,
            entities=entities,
            metadata=metadata,
            entity_keys=entity_keys,
            entity_init_params=entity_init_params,
        )

        # Pre-pass: rewrite send_async(...) into ctx.call_remote_async(...)
        self.original_func = rewrite_send_async_in_function(original_func, resolver)

        self.ctx = SplitContext(
            func_name=self.original_func.name.value,
            class_name=class_name,
            entities=entities,
            resolver=resolver,
            liveness=LivenessHelper(live_vars),
        )

        # Seed defined_vars with the function's parameters.
        for param in self.original_func.params.params:
            if param.name.value not in ("self", "ctx"):
                self.ctx.defined_vars.add(param.name.value)

    def process(self) -> list[cst.FunctionDef]:
        """Return [modified_root_func, *generated_step_funcs]."""
        body = list(self.original_func.body.body)
        new_body = self.ctx.split_body(body)

        modified = self.original_func.with_changes(body=cst.IndentedBlock(body=new_body))
        self.ctx.generated_functions.sort(key=lambda f: int(f.name.value.rsplit("_", 1)[-1]))
        return [modified, *self.ctx.generated_functions]
