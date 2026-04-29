from collections import defaultdict
from collections.abc import Callable, Sequence

import libcst as cst
from libcst import matchers as m
from libcst.metadata import QualifiedName, QualifiedNameProvider, QualifiedNameSource

from styx_compiler.control_flow import ControlFlowGraphProvider, Node
from styx_compiler.data_flow import TB, DataflowProperty, MaySet, compute_dataflow_property
from styx_compiler.metadata_providers import IndexProvider


class CollectLiveVariablesTransferFunctions(cst.CSTVisitor):
    """
    Computes the live variable analysis transfer functions for control-flow graph nodes
    """

    def __init__(self, provider: LiveVariablesDataflowPropertyProvider):
        super().__init__()
        self._provider = provider
        self._tfs: dict[Node, Callable[[frozenset[QualifiedName]], frozenset[QualifiedName]]] = defaultdict(
            lambda: lambda x: x
        )

    def resolve_name(self, node: cst.CSTNode) -> Sequence[QualifiedName] | None:
        name_origin: set[QualifiedName] = self._provider.get_metadata(QualifiedNameProvider, node)
        if len(name_origin) == 1:
            [qual_name] = name_origin
            if qual_name.source == QualifiedNameSource.LOCAL:
                return [qual_name]
        return None

    def get_dataflow_property(self) -> DataflowProperty:
        return DataflowProperty(forward=False, initial=frozenset(), transfer_func=self._tfs, lattice=MaySet())

    def _get_lhs_names(self, target: cst.BaseExpression) -> Sequence[QualifiedName]:
        if m.matches(target, m.Attribute()):
            target: cst.Attribute = cst.ensure_type(target, cst.Attribute)
            result = self.resolve_name(target)
            if result is not None:
                return result
        if m.matches(target, m.Subscript()):
            # target: cst.Subscript = cst.ensure_type(target, cst.Subscript)
            # Don't return the name of the target.value, since only the subscripted part is written to
            return []
        if m.matches(target, m.StarredElement() | m.Element()):
            return self._get_lhs_names(target.value)
        if m.matches(target, m.Name()):
            target: cst.Name = cst.ensure_type(target, cst.Name)
            result = self.resolve_name(target)
            if result is not None:
                return result
        if m.matches(target, m.List() | m.Tuple()):
            # noinspection PyUnresolvedReferences
            return [name for el in target.elements for name in self._get_lhs_names(el)]
        return []

    def leave_Module(self, module: cst.Module) -> None:
        self._provider.set_metadata(module, self.get_dataflow_property())

    def visit_Param(self, node: cst.Param) -> bool | None:
        index = self._provider.get_metadata(IndexProvider, node)
        result = self.resolve_name(node.name)
        if result is not None:
            self._tfs[Node(index, 0)] = lambda lives, names=result: lives.difference(names)

    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool | None:
        index = self._provider.get_metadata(IndexProvider, node)
        names = self._get_lhs_names(node.target)
        self._tfs[Node(index, 0)] = lambda lives, names=names: lives.difference(names)

    def visit_AssignTarget(self, node: cst.AssignTarget) -> bool | None:
        index = self._provider.get_metadata(IndexProvider, node)
        names = self._get_lhs_names(node.target)
        self._tfs[Node(index, 0)] = lambda lives, names=names: lives.difference(names)

    def visit_AugAssign(self, node: cst.AugAssign) -> bool | None:
        index = self._provider.get_metadata(IndexProvider, node)
        names = self._get_lhs_names(node.target)
        self._tfs[Node(index, 0)] = lambda lives, names=names: lives.difference(names)

    def visit_Del(self, node: cst.Del) -> bool | None:
        index = self._provider.get_metadata(IndexProvider, node)
        names = self._get_lhs_names(node.target)
        self._tfs[Node(index, 0)] = lambda lives, names=names: lives.difference(names)

    def visit_For(self, node: cst.For) -> bool | None:
        index = self._provider.get_metadata(IndexProvider, node)
        names = self._get_lhs_names(node.target)
        self._tfs[Node(index, 0)] = lambda lives, names=names: lives.difference(names)

    def visit_AsName(self, node: cst.AsName) -> bool | None:
        index = self._provider.get_metadata(IndexProvider, node)
        names = self._get_lhs_names(node.name)
        self._tfs[Node(index, 0)] = lambda lives, names=names: lives.difference(names)

    def visit_Name(self, node: cst.Name) -> bool | None:
        index = self._provider.get_metadata(IndexProvider, node)
        result = self.resolve_name(node)
        if result is not None:
            self._tfs[Node(index, 0)] = lambda lives, names=result: lives.union(names)


class LiveVariablesDataflowPropertyProvider(cst.BatchableMetadataProvider[DataflowProperty]):
    METADATA_DEPENDENCIES = (IndexProvider, QualifiedNameProvider)

    def visit_Module(self, module: cst.Module) -> None:
        module.visit(CollectLiveVariablesTransferFunctions(self))


class LiveVariablesVisitor(cst.CSTVisitor):
    def __init__(
        self,
        provider: LiveVariablesProvider,
        live_vars: dict[Node, tuple[TB[frozenset[QualifiedName]], TB[frozenset[QualifiedName]]]],
    ):
        super().__init__()
        self._provider: LiveVariablesProvider = provider
        self.live_vars: dict[Node, tuple[TB[frozenset[QualifiedName]], TB[frozenset[QualifiedName]]]] = live_vars

    def on_visit(self, node: cst.CSTNode) -> bool:
        if m.matches(node, m.SimpleWhitespace() | m.TrailingWhitespace()):
            return False
        if self._provider.get_metadata(IndexProvider, node, None) is not None:
            cfg_node = Node(self._provider.get_metadata(IndexProvider, node), 0)
            if cfg_node in self.live_vars:
                self._provider.set_metadata(node, self.live_vars[cfg_node])
        return True


class LiveVariablesProvider(
    cst.BatchableMetadataProvider[tuple[frozenset[TB[QualifiedName]], frozenset[TB[QualifiedName]]]]
):
    METADATA_DEPENDENCIES = (IndexProvider, ControlFlowGraphProvider, LiveVariablesDataflowPropertyProvider)

    def visit_Module(self, node: cst.Module) -> bool | None:
        cfg, start_end = self.get_metadata(ControlFlowGraphProvider, node)
        lv_prop = self.get_metadata(LiveVariablesDataflowPropertyProvider, node)
        lv_result = compute_dataflow_property(cfg, start_end, lv_prop)
        node.visit(LiveVariablesVisitor(self, lv_result))
