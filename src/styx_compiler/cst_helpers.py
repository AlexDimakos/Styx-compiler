"""CST construction helpers for reocurring patterns in the compiler"""

from collections.abc import Iterable

import libcst as cst

CTX_KEY = cst.Attribute(value=cst.Name("ctx"), attr=cst.Name("key"))


def quoted_str(s: str) -> cst.SimpleString:
    return cst.SimpleString(f"'{s}'")


def name_or_none(s: str | None) -> cst.BaseExpression:
    return cst.Name(s) if s else cst.Name("None")


# ── statement primitives ───────────────────────────────────────────────


def assign_stmt(target: cst.BaseExpression, value: cst.BaseExpression) -> cst.SimpleStatementLine:
    return cst.SimpleStatementLine(body=[cst.Assign(targets=[cst.AssignTarget(target=target)], value=value)])


def expr_stmt(value: cst.BaseExpression) -> cst.SimpleStatementLine:
    return cst.SimpleStatementLine(body=[cst.Expr(value=value)])


# ── containers ─────────────────────────────────────────────────────────


def dict_from_pairs(pairs: Iterable[tuple[str, cst.BaseExpression]]) -> cst.Dict:
    """Build a dict literal with single-quoted string keys."""
    return cst.Dict(elements=[cst.DictElement(key=quoted_str(k), value=v) for k, v in pairs])


def context_dict(var_names: Iterable[str]) -> cst.Dict:
    """{'v1': v1, 'v2': v2, ...} — locals to save across a split."""
    return dict_from_pairs((v, cst.Name(v)) for v in var_names)


def tuple_of(elements: Iterable[cst.BaseExpression]) -> cst.Tuple:
    return cst.Tuple(elements=[cst.Element(value=e) for e in elements])


# ── call_remote_async ──────────────────────────────────────────────────


def call_remote_async(
    operator_name: str | cst.BaseExpression,
    function_name: str | cst.BaseExpression,
    key: cst.BaseExpression,
    params: cst.BaseExpression,
) -> cst.Call:
    """ctx.call_remote_async(operator_name=..., function_name=..., key=..., params=...).

    String op/fun names are emitted as quoted literals; CST nodes are passed through.
    """
    op_val = quoted_str(operator_name) if isinstance(operator_name, str) else operator_name
    fun_val = quoted_str(function_name) if isinstance(function_name, str) else function_name
    return cst.Call(
        func=cst.parse_expression("ctx.call_remote_async"),
        args=[
            cst.Arg(keyword=cst.Name("operator_name"), value=op_val),
            cst.Arg(keyword=cst.Name("function_name"), value=fun_val),
            cst.Arg(keyword=cst.Name("key"), value=key),
            cst.Arg(keyword=cst.Name("params"), value=params),
        ],
    )


def call_remote_async_stmt(operator_name, function_name, key, params) -> cst.SimpleStatementLine:
    return expr_stmt(call_remote_async(operator_name, function_name, key, params))


# ── params tuples ──────────────────────────────────────────────────────


def params_with_reply_to(args: list[cst.BaseExpression], reply_to: cst.BaseExpression) -> cst.Tuple:
    """(arg1, arg2, ..., reply_to) — params= for a remote method call."""
    return tuple_of([*args, reply_to])


def continuation_params(
    context: cst.BaseExpression,
    result: cst.BaseExpression,
    reply_to: cst.BaseExpression,
) -> cst.Tuple:
    """(context, result, reply_to) — params= for a direct continuation call."""
    return tuple_of([context, result, reply_to])


# ── reply_to entries ───────────────────────────────────────────────────


def reply_entry(
    op_name: str,
    fun: str,
    key: cst.BaseExpression,
    context: cst.BaseExpression,
) -> cst.Dict:
    """One reply_to-stack frame: {'op_name': ..., 'fun': ..., 'id': ..., 'context': ...}."""
    return cst.Dict(
        elements=[
            cst.DictElement(key=quoted_str("op_name"), value=quoted_str(op_name)),
            cst.DictElement(key=quoted_str("fun"), value=quoted_str(fun)),
            cst.DictElement(key=quoted_str("id"), value=key),
            cst.DictElement(key=quoted_str("context"), value=context),
        ]
    )


def sink_reply_to() -> cst.List:
    """[{'sink': True}] — fire-and-forget marker swallowed by send_reply."""
    return cst.List(
        elements=[
            cst.Element(value=cst.Dict(elements=[cst.DictElement(key=quoted_str("sink"), value=cst.Name("True"))]))
        ]
    )


# ── push_continuation / init_gather_barrier ────────────────────────────


def push_continuation_stmt(
    reply_op_name: str, next_func_name: str, context: cst.BaseExpression
) -> cst.SimpleStatementLine:
    """reply_to = push_continuation(ctx, reply_to, 'op', 'fun', ctx.key, context)."""
    return assign_stmt(
        cst.Name("reply_to"),
        cst.Call(
            func=cst.Name("push_continuation"),
            args=[
                cst.Arg(value=cst.Name("ctx")),
                cst.Arg(value=cst.Name("reply_to")),
                cst.Arg(value=quoted_str(reply_op_name)),
                cst.Arg(value=quoted_str(next_func_name)),
                cst.Arg(value=CTX_KEY),
                cst.Arg(value=context),
            ],
        ),
    )


def init_gather_barrier_stmt(arity: cst.BaseExpression, saved: cst.Dict) -> cst.SimpleStatementLine:
    """_gather_id = init_gather_barrier(ctx, arity, saved, reply_to)."""
    return assign_stmt(
        cst.Name("_gather_id"),
        cst.Call(
            func=cst.Name("init_gather_barrier"),
            args=[
                cst.Arg(value=cst.Name("ctx")),
                cst.Arg(value=arity),
                cst.Arg(value=saved),
                cst.Arg(value=cst.Name("reply_to")),
            ],
        ),
    )


# ── restore block ──────────────────────────────────────────────────────


def restore_tuple_from(source: str, var_names: list[str]) -> cst.SimpleStatementLine:
    """(v1, v2, ...) = (source.get('v1'), source.get('v2'), ...)."""
    targets = cst.Tuple(elements=[cst.Element(cst.Name(v)) for v in var_names])
    values = cst.Tuple(
        elements=[
            cst.Element(
                cst.Call(
                    func=cst.Attribute(value=cst.Name(source), attr=cst.Name("get")),
                    args=[cst.Arg(value=quoted_str(v))],
                )
            )
            for v in var_names
        ]
    )
    return assign_stmt(targets, values)


def resolve_context_stmt() -> cst.SimpleStatementLine:
    """params = resolve_context(ctx, func_context)."""
    return assign_stmt(
        cst.Name("params"),
        cst.Call(
            func=cst.Name("resolve_context"),
            args=[cst.Arg(value=cst.Name("ctx")), cst.Arg(value=cst.Name("func_context"))],
        ),
    )


# ── continuation function def ──────────────────────────────────────────


def continuation_func(name: str, body: list, target_var: str, op_name: str) -> cst.FunctionDef:
    """Build an `async def <func_name>(ctx, func_context, <target_var>=None, reply_to: list = None)`
    decorated with operator.register."""
    operator = op_name + "_operator"
    deco = cst.Decorator(decorator=cst.parse_expression(f"{operator}.register"))
    reply_to_param = cst.Param(
        name=cst.Name("reply_to"),
        annotation=cst.Annotation(annotation=cst.Name("list")),
        default=cst.Name("None"),
    )
    return cst.FunctionDef(
        name=cst.Name(name),
        params=cst.Parameters(
            params=[
                cst.Param(
                    name=cst.Name("ctx"),
                    annotation=cst.Annotation(cst.Name("StatefulFunction")),
                ),
                cst.Param(name=cst.Name("func_context")),
                cst.Param(name=cst.Name(target_var), default=cst.Name("None")),
                reply_to_param,
            ]
        ),
        body=cst.IndentedBlock(body=body),
        decorators=[deco],
        asynchronous=cst.Asynchronous(),
    )
