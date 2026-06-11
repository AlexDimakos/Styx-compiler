"""
Main Styx transpiler implementation.
"""

import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

import libcst as cst
import libcst.matchers as m
import mypy.api
from libcst import CSTNode, FlattenSentinel, FunctionDef, Module, RemovalSentinel
from libcst_mypy import MypyTypeInferenceProvider
from libcst_mypy.utils import MypyType

from styx_compiler.comprehension_expander import ComprehensionExpander
from styx_compiler.config import N_PARTITIONS
from styx_compiler.live_variables import LiveVariablesProvider
from styx_compiler.processor import FunctionProcessor
from styx_compiler.transformers import (
    EntityTypeReplacer,
    RemoteCallLinearizer,
    ReturnHandlerTransformer,
    ShortCircuitRewriter,
    StateAccessTransformer,
    normalize_function_body,
)
from styx_compiler.visitor import EntityDiscoveryVisitor


def _uses_state(node: cst.CSTNode) -> bool:
    """Recursively checks whether any Name('__state__') appears in the CST subtree."""
    if isinstance(node, cst.Name) and node.value == "__state__":
        return True
    return any(_uses_state(child) for child in node.children)


def _is_gather_join(func: cst.FunctionDef) -> bool:
    """Gather-join continuations are emitted with a `_gather_partial` parameter."""
    return any(p.name.value == "_gather_partial" for p in func.params.params)


def _gather_state_load_index(func: cst.FunctionDef, body_list: list) -> int:
    """Where to insert `__state__ = ctx.get() or {}` in a step body so that we don't
    create many deep copies of the state before all results are collected."""

    if not _is_gather_join(func):
        return 0
    for idx, stmt in enumerate(body_list):
        if isinstance(stmt, cst.If):
            return idx + 1
    return 0


class StyxTransformer(cst.CSTTransformer):
    """
    Main transformer that processes entity classes and converts them to Styx operators.
    """

    def __init__(
        self,
        entities: dict[str, str],
        metadata: Mapping,
        entity_keys: dict[str, list[str]] | None = None,
        entity_init_params: dict[str, list[str]] | None = None,
        live_vars: Mapping | None = None,
    ):
        super().__init__()
        self.entities = entities
        self.metadata = metadata
        self.entity_keys = entity_keys or {}
        self.entity_init_params = entity_init_params or {}
        self.live_vars = live_vars or {}
        self.current_operator = None

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        if node.name.value in self.entities:
            self.current_operator = node.name.value
            return True
        return False

    def leave_Module(self, _original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        imports = [
            cst.SimpleStatementLine(body=[cst.parse_statement("import uuid").body[0]]),
            cst.SimpleStatementLine(body=[cst.parse_statement("from styx.common.operator import Operator").body[0]]),
            cst.SimpleStatementLine(
                body=[cst.parse_statement("from styx.common.stateful_function import StatefulFunction").body[0]]
            ),
            cst.SimpleStatementLine(body=[cst.parse_statement("from styx.common.logging import logging").body[0]]),
            cst.EmptyLine(),
        ]

        helpers_code = """
def send_reply(ctx: StatefulFunction, reply_to: list, result):
    if reply_to:
        reply_info = reply_to[-1]
        if isinstance(reply_info, dict) and reply_info.get("sink"):
            return
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], result, reply_to[:-1]),
        )
    else:
        return result


def push_continuation(
    ctx: StatefulFunction, reply_to: list, op_name: str, fun: str, step_id: str, context: dict
) -> list:
    context_dict = ctx.get_func_context() or {}
    next_id = context_dict.get("next_id", 0)
    context_dict["next_id"] = next_id + 1

    context_dict[next_id] = context
    ctx.put_func_context(context_dict)
    if reply_to is None:
        reply_to = []
    reply_to.append(
        {
            "op_name": op_name,
            "fun": fun,
            "id": step_id,
            "context": next_id,
        }
    )
    return reply_to


def resolve_context(ctx: StatefulFunction, context_data) -> dict:
    if isinstance(context_data, dict):
        return context_data

    ctx_dict = ctx.get_func_context() or {}
    params = ctx_dict.pop(context_data)
    ctx.put_func_context(ctx_dict)
    return params


def init_gather_barrier(ctx: StatefulFunction, total: int, saved: dict, parent_reply_to) -> str:
    ctx_dict = ctx.get_func_context() or {}
    counter = ctx_dict.get("_gather_counter", 0)
    barrier_id = "_gather_" + str(counter)
    ctx_dict["_gather_counter"] = counter + 1
    ctx_dict[barrier_id] = {
        "total": total,
        "pending": {},
        "saved": saved,
        "parent_reply_to": parent_reply_to,
    }
    ctx.put_func_context(ctx_dict)
    return barrier_id


def update_gather_barrier(ctx: StatefulFunction, barrier_id: str, tag, result):
    ctx_dict = ctx.get_func_context() or {}
    barrier = ctx_dict[barrier_id]
    if barrier["total"] == 0:
        ctx_dict.pop(barrier_id)
        ctx.put_func_context(ctx_dict)
        return True, (), barrier["saved"], barrier["parent_reply_to"]
    barrier["pending"][tag] = result
    if len(barrier["pending"]) == barrier["total"]:
        ctx_dict.pop(barrier_id)
        ctx.put_func_context(ctx_dict)
        results = tuple(barrier["pending"][i] for i in range(barrier["total"]))
        return True, results, barrier["saved"], barrier["parent_reply_to"]
    ctx.put_func_context(ctx_dict)
    return False, None, None, None
"""
        helpers_module = cst.parse_module(helpers_code)
        helpers = [*list(helpers_module.body), cst.EmptyLine()]

        # Filter out stuff for mytype (entity function, logging class) from body
        stub_names = {"entity", "logging", "send_async"}

        # Matches `from styx_compiler.api import …` and `from styx_compiler import api[, …]`.
        styx_api_dotted = m.SimpleStatementLine(
            body=[
                m.ZeroOrMore(),
                m.ImportFrom(module=m.Attribute(value=m.Name("styx_compiler"), attr=m.Name("api"))),
                m.ZeroOrMore(),
            ]
        )
        styx_api_bare = m.SimpleStatementLine(
            body=[
                m.ZeroOrMore(),
                m.ImportFrom(
                    module=m.Name("styx_compiler"),
                    names=[m.ZeroOrMore(), m.ImportAlias(name=m.Name("api")), m.ZeroOrMore()],
                ),
                m.ZeroOrMore(),
            ]
        )

        def is_styx_api_import(node: cst.CSTNode) -> bool:
            return m.matches(node, styx_api_dotted) or m.matches(node, styx_api_bare)

        filtered_body = [
            stmt
            for stmt in updated_node.body
            if not (
                (isinstance(stmt, cst.FunctionDef) and stmt.name.value in stub_names)
                or (isinstance(stmt, cst.ClassDef) and stmt.name.value in stub_names)
                or is_styx_api_import(stmt)
            )
        ]

        new_body = list(imports) + helpers + filtered_body
        return updated_node.with_changes(body=new_body)

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef | cst.FlattenSentinel:
        if original_node.name.value not in self.entities:
            return updated_node

        op_name = self.entities[original_node.name.value]

        entity_name = original_node.name.value
        keys = self.entity_keys.get(entity_name, [])
        if len(keys) > 1:
            op_def_code = (
                f"{op_name}_operator = Operator("
                f"'{op_name}', n_partitions={N_PARTITIONS}, composite_key_hash_params=(0, ':'))"
            )
        else:
            op_def_code = f"{op_name}_operator = Operator('{op_name}', n_partitions={N_PARTITIONS})"
        op_def_node = cst.parse_statement(op_def_code)

        new_nodes = [op_def_node, cst.EmptyLine()]

        for statement in updated_node.body.body:
            if isinstance(statement, (cst.FunctionDef, cst.ClassDef)):
                new_nodes.append(statement)
                new_nodes.append(cst.EmptyLine())

        return cst.FlattenSentinel(new_nodes)

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> FunctionDef | RemovalSentinel | FlattenSentinel:
        if self.current_operator is None:
            return updated_node

        # Normalize inline function bodies (SimpleStatementSuite -> IndentedBlock)
        original_node = normalize_function_body(original_node)
        updated_node = normalize_function_body(updated_node)

        func_name = original_node.name.value

        if func_name == "__key__":
            return cst.RemoveFromParent()
        if func_name == "__init__":
            prepared = self._prepare_init(updated_node)
            return self._process_and_finalize(prepared, prepared, is_init=True)
        return self._process_and_finalize(original_node, updated_node, is_init=False)

    def _operator_decorator(self) -> cst.Decorator:
        op_name = self.entities[self.current_operator] + "_operator"
        return cst.Decorator(decorator=cst.Attribute(value=cst.Name(op_name), attr=cst.Name("register")))

    def _prepare_init(self, node: cst.FunctionDef) -> cst.FunctionDef:
        """Convert __init__ into the entry-point `insert` step before splitting:
        rename, drop self, add ctx + reply_to, wrap body with state init and
        `return ctx.key`, become async, attach operator decorator. Any remote
        calls inside __init__ get sliced normally by FunctionProcessor afterwards.
        """
        ctx_param = cst.Param(name=cst.Name("ctx"), annotation=cst.Annotation(annotation=cst.Name("StatefulFunction")))
        reply_to_param = cst.Param(
            name=cst.Name("reply_to"),
            annotation=cst.Annotation(annotation=cst.Name("list")),
            default=cst.Name("None"),
        )
        new_params = [ctx_param] + [p for p in node.params.params if p.name.value != "self"] + [reply_to_param]

        init_state = cst.parse_statement("__state__ = {}")
        put_func_state = cst.parse_statement("ctx.put_func_context({})")
        return_stmt = cst.parse_statement("return ctx.key")
        new_block = node.body.with_changes(body=[init_state, *list(node.body.body), put_func_state, return_stmt])

        return node.with_changes(
            name=cst.Name("insert"),
            params=node.params.with_changes(params=new_params),
            body=new_block,
            asynchronous=cst.Asynchronous(),
            decorators=[self._operator_decorator()],
        )

    def _process_and_finalize(
        self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef,
        is_init: bool,
    ) -> cst.FlattenSentinel:
        """Split the function into steps and post-process each one uniformly."""
        processor = FunctionProcessor(
            original_node,
            self.current_operator,
            self.entities,
            self.metadata,
            self.entity_keys,
            self.entity_init_params,
            self.live_vars,
        )
        new_functions = processor.process()

        final_nodes: list[cst.CSTNode] = []
        for func in new_functions:
            is_root = func.name.value == original_node.name.value

            transformed_func = func.visit(
                StateAccessTransformer(self.metadata, self.entity_keys, self.entity_init_params)
            )

            # Continuations (and non-init roots) that touch `__state__` must load it
            # from ctx. Skip for the init root — its body starts with `__state__ = {}`.
            # `or {}` covers the fresh-key case (e.g. method called before any insert).
            if _uses_state(transformed_func) and not (is_init and is_root):
                get_state = cst.parse_statement("__state__ = ctx.get() or {}")
                body_list = list(transformed_func.body.body)
                insert_at = _gather_state_load_index(transformed_func, body_list)
                body_list.insert(insert_at, get_state)
                transformed_func = transformed_func.with_changes(body=cst.IndentedBlock(body=body_list))

            transformed_func = transformed_func.visit(
                ReturnHandlerTransformer(uses_state=_uses_state(transformed_func))
            )

            # Method root: its signature still has `self` and no decorator. Init root
            # already had params/decorator/async set in _prepare_init.
            if is_root and not is_init:
                transformed_func = self._finalize_original_signature(transformed_func, updated_node)

            final_nodes.append(transformed_func)
            final_nodes.append(cst.EmptyLine())

        return cst.FlattenSentinel(final_nodes)

    def _finalize_original_signature(self, node: cst.FunctionDef, reference_node: cst.FunctionDef):
        ctx_param = cst.Param(name=cst.Name("ctx"), annotation=cst.Annotation(cst.Name("StatefulFunction")))
        reply_to_param = cst.Param(
            name=cst.Name("reply_to"), annotation=cst.Annotation(cst.Name("list")), default=cst.Name("None")
        )

        new_params = (
            [ctx_param] + [p for p in reference_node.params.params if p.name.value != "self"] + [reply_to_param]
        )

        return node.with_changes(
            params=node.params.with_changes(params=new_params),
            decorators=[self._operator_decorator()],
            asynchronous=cst.Asynchronous(),
        )


class StyxTranspiler:
    """
    Main transpiler class that orchestrates the transformation process.
    """

    def __init__(self, source_code: str):
        self.source_code = source_code
        self.cst_tree = cst.parse_module(source_code)
        self.entities: dict[str, str] = {}
        self.entity_keys = None
        self.entity_init_params = None

    def run(self) -> str:
        """
        Run the transpilation process.

        Returns:
            str: The transpiled code
        """
        print("--- Starting Transpilation ---")

        # 1. Discover entities
        visitor = EntityDiscoveryVisitor()
        self.cst_tree.visit(visitor)
        self.entities = visitor.entities
        self.entity_keys = visitor.entity_keys
        self.entity_key_types = visitor.entity_key_types
        self.entity_init_params = visitor.entity_init_params
        print(f"Identified {len(self.entities)} stateful entities:", list(self.entities.keys()))

        # 1.5. Expand comprehensions into for loops and rewrite short-circuiting BoolOps with remote calls into
        # explicit ifs, to maintain short circuit semantics
        expander = ComprehensionExpander(self.entities)
        self.cst_tree = self.cst_tree.visit(expander)

        sc_rewriter = ShortCircuitRewriter(self.entities)
        self.cst_tree = self.cst_tree.visit(sc_rewriter)

        # 2. Linearize
        linearizer = RemoteCallLinearizer(self.entities)
        linearized_tree = self.cst_tree.visit(linearizer)
        linearized_code = linearized_tree.code

        # 3. Run mypy + live variable analysis on the linearized code to get type and live variable metadata
        module, metadata, live_vars = StyxTranspiler._resolve_types(linearized_code)

        # 4. Transform using the same node tree (metadata lookups match)
        transformer = StyxTransformer(self.entities, metadata, self.entity_keys, self.entity_init_params, live_vars)
        modified_tree = module.visit(transformer)

        # 5. Replace entity type annotations with key types
        type_replacer = EntityTypeReplacer(self.entity_keys, self.entity_init_params, self.entity_key_types)
        modified_tree = modified_tree.visit(type_replacer)

        return modified_tree.code

    # TODO: This should probably be fixed at some point
    # Minimal stubs written alongside the source so mypy resolves
    # `from styx_compiler.api import …` locally instead of following the
    # import into the full installed package.
    _API_STUBS = (
        "from typing import TypeVar, Type, Callable, Any, Tuple\n"
        "T = TypeVar('T')\n"
        "def entity(cls: Type[T]) -> Type[T]: return cls\n"
        "def user_operator(func: Callable) -> Callable: return func\n"
        "def send_async(remote_call: Any) -> None: ...\n"
        "def get_entity_by_key(entity_class: Type[T], key: Any) -> T: ...\n"
        "def gather(*args: Any) -> Tuple[Any, ...]: ...\n"
        "def exists(entity: Any) -> bool: ...\n"
    )

    @staticmethod
    def _resolve_types(source_code: str) -> tuple[Module, Mapping[CSTNode, MypyType], Mapping]:
        """
        Run mypy on the source code and return (parsed_module, type_metadata, live_var_metadata).
        The type_metadata maps cst.CSTNode -> MypyType.
        The live_var_metadata maps cst.CSTNode -> (live_in, live_out).
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / "source.py"
            tmp_path.write_text(source_code, encoding="utf-8")

            # Write a minimal local styx_compiler stub package so mypy only
            # type-checks our tiny API stubs, not the entire installed package.
            stub_pkg = Path(tmp_dir) / "styx_compiler"
            stub_pkg.mkdir()
            (stub_pkg / "__init__.py").write_text("", encoding="utf-8")
            (stub_pkg / "api.py").write_text(StyxTranspiler._API_STUBS, encoding="utf-8")

            # Point MYPYPATH at tmp_dir so mypy finds our local stub package
            # first and never traverses the full installed styx_compiler package.
            old_mypypath = os.environ.get("MYPYPATH")
            os.environ["MYPYPATH"] = str(tmp_dir)
            try:
                # 1. First run: type-check and surface user errors
                stdout, _stderr, exit_code = mypy.api.run(
                    [
                        "--disable-error-code=func-returns-value",
                        "--follow-imports=silent",
                        "--ignore-missing-imports",
                        "--no-error-summary",
                        str(tmp_path),
                    ]
                )
                if exit_code != 0:
                    clean_errs = stdout.replace(str(tmp_path), "source")
                    msg = f"Mypy Type Check Failed:\n{clean_errs}"
                    raise RuntimeError(msg)

                # 2. Second run: generate metadata cache
                cache = MypyTypeInferenceProvider.gen_cache(
                    root_path=tmp_path.parent,
                    paths=[str(tmp_path)],
                )

                module = cst.parse_module(source_code)
                file_cache = cache.get(str(tmp_path))
                if not file_cache:
                    msg = "Mypy failed to generate type metadata. Ensure the code is type-safe."
                    raise RuntimeError(msg)

                wrapper = cst.metadata.MetadataWrapper(
                    module,
                    unsafe_skip_copy=True,
                    cache={MypyTypeInferenceProvider: file_cache},
                )
                metadata = wrapper.resolve(MypyTypeInferenceProvider)
                live_vars = wrapper.resolve(LiveVariablesProvider)

                return wrapper.module, metadata, live_vars
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                msg = f"Type resolution failed: {e}"
                raise RuntimeError(msg) from e
            finally:
                if old_mypypath is None:
                    os.environ.pop("MYPYPATH", None)
                else:
                    os.environ["MYPYPATH"] = old_mypypath


# Main execution
def main():
    file_name = sys.argv[1] if len(sys.argv) > 1 else "user_item.py"
    input_file = Path("examples/original") / file_name
    output_file = Path("examples/compiled") / file_name

    try:
        code = input_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"Error: The file '{input_file}' was not found.")
        sys.exit(1)

    transpiler = StyxTranspiler(code)
    output_code = transpiler.run()

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(output_code, encoding="utf-8")

    print(f"Successfully transpiled '{input_file}' to '{output_file}'")


if __name__ == "__main__":
    main()
