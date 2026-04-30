"""Tests for the ComprehensionExpander transformer."""

import libcst as cst

from styx_compiler.comprehension_expander import ComprehensionExpander


def _expand(code: str) -> str:
    """Parse, expand comprehensions, return resulting code."""
    tree = cst.parse_module(code)
    expander = ComprehensionExpander()
    new_tree = tree.visit(expander)
    return new_tree.code


def test_list_comprehension():
    code = "result = [x * 2 for x in items]\n"
    output = _expand(code)
    assert "for x in items:" in output
    assert ".append(x * 2)" in output
    assert "_comp_result_1" in output
    assert "[x * 2 for x in items]" not in output


def test_dict_comprehension():
    code = "result = {k: v for k, v in pairs}\n"
    output = _expand(code)
    assert "for k, v in pairs:" in output
    assert "_comp_result_1" in output
    assert "{k: v for k, v in pairs}" not in output


def test_set_comprehension():
    code = "result = {x for x in items}\n"
    output = _expand(code)
    assert "for x in items:" in output
    assert ".add(x)" in output
    assert "_comp_result_1" in output
    assert "{x for x in items}" not in output


def test_list_comprehension_with_filter():
    code = "result = [x for x in items if x > 0]\n"
    output = _expand(code)
    assert "for x in items:" in output
    assert "if x > 0:" in output
    assert ".append(x)" in output


def test_nested_comprehension():
    code = "result = [x + y for x in xs for y in ys]\n"
    output = _expand(code)
    assert "for x in xs:" in output
    assert "for y in ys:" in output
    assert ".append(x + y)" in output


def test_no_comprehension_unchanged():
    code = "x = 42\n"
    output = _expand(code)
    assert output.strip() == "x = 42"


def test_multiple_comprehensions_in_function():
    code = """\
def f():
    a = [x for x in items]
    b = {x: x for x in items}
    c = {x for x in items}
"""
    output = _expand(code)
    assert "_comp_result_1" in output
    assert "_comp_result_2" in output
    assert "_comp_result_3" in output


def test_comprehension_with_method_call():
    """Matches the user_item.py example: item.get_stock() for item in items."""
    code = "stock_values = [item.get_stock() for item in items]\n"
    output = _expand(code)
    assert "for item in items:" in output
    assert ".append(item.get_stock())" in output
    assert "_comp_result_1" in output
