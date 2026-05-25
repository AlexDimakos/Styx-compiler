import uuid
from styx.common.operator import Operator
from styx.common.stateful_function import StatefulFunction
from styx.common.logging import logging

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
    barrier["pending"][tag] = result
    if len(barrier["pending"]) == barrier["total"]:
        ctx_dict.pop(barrier_id)
        ctx.put_func_context(ctx_dict)
        results = tuple(barrier["pending"][i] for i in range(barrier["total"]))
        return True, results, barrier["saved"], barrier["parent_reply_to"]
    ctx.put_func_context(ctx_dict)
    return False, None, None, None

from styx_compiler.core import entity, send_async

class NotEnoughCredit(Exception):
    pass
ycsb_operator = Operator('ycsb', n_partitions=4)

@ycsb_operator.register
async def insert(ctx: StatefulFunction, key: str, reply_to: list = None):
    __state__ = {}
    __state__['key'] = key
    __state__['value'] = 1_000_000
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@ycsb_operator.register
async def get_key(ctx: StatefulFunction, reply_to: list = None) -> str:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['key'])


@ycsb_operator.register
async def get_value(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['value'])


@ycsb_operator.register
async def set_value(ctx: StatefulFunction, value: int, reply_to: list = None):
    __state__ = ctx.get() or {}
    __state__['value'] = value
    ctx.put(__state__)


@ycsb_operator.register
async def read(ctx: StatefulFunction, reply_to: list = None) -> tuple[str, int]:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, (ctx.key, __state__['value']))


@ycsb_operator.register
async def update(ctx: StatefulFunction, reply_to: list = None) -> tuple[str, int]:
    __state__ = ctx.get() or {}
    __state__['value'] += 1
    ctx.put(__state__)
    return send_reply(ctx, reply_to, (ctx.key, __state__['value']))



@ycsb_operator.register
async def transfer(ctx: StatefulFunction, key_b: str, reply_to: list = None) -> tuple[str, int]:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'ycsb', function_name = 'update', key = key_b, params = ([{'sink': True}],))
    __state__['value'] -= 1
    if __state__['value'] < 0:
        raise NotEnoughCredit(f"Not enough credit for user: {ctx.key}")
    ctx.put(__state__)
    return send_reply(ctx, reply_to, (ctx.key, __state__['value']))

