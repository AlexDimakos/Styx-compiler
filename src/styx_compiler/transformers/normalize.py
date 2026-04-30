"""CST shape normalization helpers used by multiple later passes."""

import libcst as cst


def normalize_function_body(node: cst.FunctionDef) -> cst.FunctionDef:
    """Convert inline functions to normal functions"""
    if isinstance(node.body, cst.SimpleStatementSuite):
        new_body = cst.IndentedBlock(body=[cst.SimpleStatementLine(body=list(node.body.body))])
        return node.with_changes(body=new_body)
    return node


def normalize_inline_if(node: cst.If) -> cst.If:
    """Normalize inline if statements into regular if statements."""
    # Normalize the if-body
    if isinstance(node.body, cst.SimpleStatementSuite):
        new_body = cst.IndentedBlock(body=[cst.SimpleStatementLine(body=list(node.body.body))])
        node = node.with_changes(body=new_body)

    # Normalize the else/elif
    if node.orelse is not None:
        if isinstance(node.orelse, cst.Else) and isinstance(node.orelse.body, cst.SimpleStatementSuite):
            new_else_body = cst.IndentedBlock(body=[cst.SimpleStatementLine(body=list(node.orelse.body.body))])
            node = node.with_changes(orelse=node.orelse.with_changes(body=new_else_body))
        elif isinstance(node.orelse, cst.If):
            node = node.with_changes(orelse=normalize_inline_if(node.orelse))

    return node
