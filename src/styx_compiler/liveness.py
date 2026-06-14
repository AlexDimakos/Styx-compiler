"""Live-variable analysis metadata helpers for the splitter.

Extracts exact variable names from the metadata and provides utilities for computing
which variables need to be saved at specific split points such as loop entry or exit.
"""

from collections.abc import Mapping

import libcst as cst
from libcst import matchers as m
from libcst_dfa.data_flow import ImmutableSet


class LivenessHelper:
    def __init__(self, live_vars: Mapping | None):
        self.live_vars: dict = dict(live_vars) if live_vars is not None else {}

    @staticmethod
    def simple_name(v) -> str:
        """Convert QualifiedName to the variable name."""
        if hasattr(v, "name"):
            # QualifiedName .name may look like 'module.func.<locals>.var'.
            return str(v.name).split(".")[-1]
        return str(v).split(".")[-1] if "." in str(v) else str(v)

    def _live_set(self, node: cst.CSTNode | None, kind: str) -> ImmutableSet | None:
        if not self.live_vars or node is None:
            return None
        data = self.live_vars.get(node)
        if data is None:
            return None
        val = data[0] if kind == "in" else data[1]
        return val if isinstance(val, ImmutableSet) else None

    def live_out(self, stmt: cst.CSTNode) -> ImmutableSet | None:
        """Live-out set for a statement, or None if no liveness data."""
        if isinstance(stmt, cst.SimpleStatementLine) and stmt.body:
            element = stmt.body[0]
            if isinstance(element, cst.Assign) and element.targets:
                live = self._live_set(element.targets[0], "out")
                if live is not None:
                    return live
            elif isinstance(element, cst.Expr) and isinstance(element.value, cst.Call):
                live = self._live_set(element.value, "out")
                if live is not None:
                    return live
            elif isinstance(element, cst.AugAssign):
                live = self._live_set(element, "out")
                if live is not None:
                    return live
        return self._live_set(stmt, "out")

    def live_in_at_loop(self, loop_stmt: cst.For | cst.While) -> set[str] | None:
        """Vars live just before re-entering a loop iteration."""
        if isinstance(loop_stmt, cst.For):
            live = self._live_set(loop_stmt, "in")
            if live is None:
                return None
            result = {self.simple_name(qn) for qn in live}
            result.update(n.value for n in m.findall(loop_stmt.iter, m.Name()))
            return result

        if isinstance(loop_stmt, cst.While):
            live = self._live_set(loop_stmt.test, "in")
            if live is None:
                return None
            return {self.simple_name(qn) for qn in live}
        return None

    def live_out_at_loop(self, loop_stmt: cst.For | cst.While) -> set[str] | None:
        """Vars live just after exiting the loop"""
        node = loop_stmt if isinstance(loop_stmt, cst.For) else loop_stmt.test
        live = self._live_set(node, "out")
        if live is None:
            return None
        return {self.simple_name(qn) for qn in live}

    def add_synthetic_loop_vars(self, vars_set: set[str], defined_vars: set[str]) -> None:
        """Always include synthetic loop index / comp-result vars (generated after the liveness analysis pass)."""
        for v in defined_vars:
            if v.startswith(("__loop_index_", "_comp_result_")):
                vars_set.add(v)

    def vars_to_save_at(self, stmt: cst.CSTNode, defined_vars: set[str]) -> set[str]:
        """Variables to save at each split point.

        We use `defined_vars` both as a fallback when liveness data is missing
        and to handle cases where scopes differ across conditionals. For example,
        a variable may be defined in only one branch of an `if` statement, while
        liveness analysis might mark it as live in both branches. Using
        `defined_vars` allows us to distinguish between these cases.
        """
        live_out = self.live_out(stmt)
        if live_out is not None:
            live_names = {self.simple_name(qn) for qn in live_out}
            result = live_names & defined_vars
            self.add_synthetic_loop_vars(result, defined_vars)
            return result
        return set(defined_vars)

    @staticmethod
    def collect_assigned_vars(stmts: list) -> set[str]:
        """All variable names assigned anywhere in `stmts`."""
        result: set[str] = set()
        for stmt in stmts:
            if isinstance(stmt, cst.SimpleStatementLine):
                for element in stmt.body:
                    if isinstance(element, cst.Assign):
                        for target in element.targets:
                            if isinstance(target.target, cst.Name) and target.target.value != "__state__":
                                result.add(target.target.value)
                    elif isinstance(element, cst.AugAssign):
                        if isinstance(element.target, cst.Name) and element.target.value != "__state__":
                            result.add(element.target.value)
                    elif (
                        isinstance(element, cst.AnnAssign)
                        and isinstance(element.target, cst.Name)
                        and element.value is not None
                        and element.target.value != "__state__"
                    ):
                        result.add(element.target.value)
            elif isinstance(stmt, cst.If):
                result.update(LivenessHelper.collect_assigned_vars(list(stmt.body.body)))
                if stmt.orelse:
                    if isinstance(stmt.orelse, cst.Else):
                        result.update(LivenessHelper.collect_assigned_vars(list(stmt.orelse.body.body)))
                    elif isinstance(stmt.orelse, cst.If):
                        result.update(LivenessHelper.collect_assigned_vars([stmt.orelse]))
            elif isinstance(stmt, (cst.For, cst.While)):
                result.update(LivenessHelper.collect_assigned_vars(list(stmt.body.body)))
                if isinstance(stmt, cst.For):
                    if isinstance(stmt.target, cst.Name):
                        result.add(stmt.target.value)
                    elif isinstance(stmt.target, cst.Tuple):
                        for elem in stmt.target.elements:
                            if isinstance(elem.value, cst.Name):
                                result.add(elem.value.value)
        return result
