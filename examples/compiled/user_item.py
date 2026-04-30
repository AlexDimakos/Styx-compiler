import uuid
from styx.common.operator import Operator
from styx.common.stateful_function import StatefulFunction
from styx.common.logging import logging

def send_reply(ctx: StatefulFunction, reply_to: list, result):
    if reply_to:
        reply_info = reply_to[-1]
        if isinstance(reply_info, dict) and reply_info.get("sink"):
            return
        reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], result, reply_to),
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

from typing import Optional
import logging
from typing import TypeVar, Type, Callable, Any



class NotEnoughBalance(Exception):
    pass


class OutOfStock(Exception):
    pass
coupon_operator = Operator('coupon', n_partitions=4)

@coupon_operator.register
async def insert(ctx: StatefulFunction, code: str, discount: int, reply_to: list = None):
    __state__ = {}
    __state__['code'] = code
    __state__['discount'] = discount
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@coupon_operator.register
async def get_discount(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['discount'])

item_operator = Operator('item', n_partitions=4)

@item_operator.register
async def insert(ctx: StatefulFunction, item_name: str, price: int, reply_to: list = None):
    __state__ = {}
    __state__['item_name'] = item_name
    __state__['stock'] = 0
    __state__['price'] = price
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@item_operator.register
async def get_price(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['price'])


@item_operator.register
async def get_stock(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['stock'])


@item_operator.register
async def update_stock(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if (__state__['stock'] + amount) < 0:
        raise OutOfStock("Not enough stock to update.")
    __state__['stock'] += amount
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)

user_operator = Operator('user', n_partitions=4)

@user_operator.register
async def insert(ctx: StatefulFunction, username: str, reply_to: list = None):
    __state__ = {}
    __state__['username'] = username
    __state__['balance'] = 0
    __state__['myitems'] = []
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@user_operator.register
async def get_balance(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['balance'])


@user_operator.register
async def get_items(ctx: StatefulFunction, reply_to: list = None) -> list[str]:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['myitems'])


@user_operator.register
async def add_balance(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    __state__['balance'] += amount
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)



@user_operator.register
async def simple_loop(ctx: StatefulFunction, items: list[str], reply_to: list = None) -> int:
    total = 0
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'user', function_name = 'simple_loop_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'items': items, 'total': total}, None, reply_to))

@user_operator.register
async def simple_loop_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, items, total) = (params.get('__loop_index_1'), params.get('items'), params.get('total'))
    if __loop_index_1 >= len(items):
        ctx.call_remote_async(operator_name = 'user', function_name = 'simple_loop_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'items': items, 'total': total}, None, reply_to))
    else:
        item = items[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'user', 'simple_loop_step_4', ctx.key, {'__loop_index_1': __loop_index_1, 'items': items, 'total': total})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))

@user_operator.register
async def simple_loop_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, items, total) = (params.get('__loop_index_1'), params.get('items'), params.get('total'))
    print(total)
    return send_reply(ctx, reply_to, total)

@user_operator.register
async def simple_loop_step_4(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, items, total) = (params.get('__loop_index_1'), params.get('items'), params.get('total'))
    total += attr_1
    ctx.call_remote_async(operator_name = 'user', function_name = 'simple_loop_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'items': items, 'total': total}, None, reply_to))


@user_operator.register
async def buy_item(ctx: StatefulFunction, amount: int, item: str, reply_to: list = None) -> bool:
    reply_to = push_continuation(ctx, reply_to, 'user', 'buy_item_step_2', ctx.key, {'amount': amount, 'item': item})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))

@user_operator.register
async def buy_item_step_2(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (amount, item) = (params.get('amount'), params.get('item'))
    total_price = amount * attr_1

    if __state__['balance'] < total_price:
        raise NotEnoughBalance("Not enough balance to buy the item.")
    reply_to = push_continuation(ctx, reply_to, 'user', 'buy_item_step_3', ctx.key, {'item': item, 'total_price': total_price})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (-amount, reply_to))

@user_operator.register
async def buy_item_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (item, total_price) = (params.get('item'), params.get('total_price'))
    __state__['balance'] -= total_price
    __state__['myitems'].append(item)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@user_operator.register
async def drain_stock(ctx: StatefulFunction, item: str, reply_to: list = None) -> int:
    total = 0
    ctx.call_remote_async(operator_name = 'user', function_name = 'drain_stock_step_2', key = ctx.key, params = ({'item': item, 'total': total}, None, reply_to))

@user_operator.register
async def drain_stock_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (item, total) = (params.get('item'), params.get('total'))
    reply_to = push_continuation(ctx, reply_to, 'user', 'drain_stock_step_4', ctx.key, {'item': item, 'total': total})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = item, params = (reply_to,))

@user_operator.register
async def drain_stock_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (item, total) = (params.get('item'), params.get('total'))
    return send_reply(ctx, reply_to, total)

@user_operator.register
async def drain_stock_step_4(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (item, total) = (params.get('item'), params.get('total'))
    if not (0 < (attr_2 - 1)):
        ctx.call_remote_async(operator_name = 'user', function_name = 'drain_stock_step_3', key = ctx.key, params = ({'item': item, 'total': total}, None, reply_to))
    else:
        reply_to = push_continuation(ctx, reply_to, 'user', 'drain_stock_step_5', ctx.key, {'item': item, 'total': total})
        ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (-1, reply_to))

@user_operator.register
async def drain_stock_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (item, total) = (params.get('item'), params.get('total'))
    total += 1
    ctx.call_remote_async(operator_name = 'user', function_name = 'drain_stock_step_2', key = ctx.key, params = ({'item': item, 'total': total}, None, reply_to))



@user_operator.register
async def bulk_purchase_with_tiers(ctx: StatefulFunction, cart: list[str], quantities: list[int], reply_to: list = None) -> str:
    total_cost = 0
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cart': cart, 'quantities': quantities, 'total_cost': total_cost}, None, reply_to))

@user_operator.register
async def bulk_purchase_with_tiers_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cart, quantities, total_cost) = (params.get('__loop_index_1'), params.get('cart'), params.get('quantities'), params.get('total_cost'))
    if __loop_index_1 >= len(cart):
        ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cart': cart, 'quantities': quantities, 'total_cost': total_cost}, None, reply_to))
    else:
        index = __loop_index_1
        __loop_index_1 += 1
        item = cart[index]
        requested_amount = quantities[index]
        reply_to = push_continuation(ctx, reply_to, 'user', 'bulk_purchase_with_tiers_step_4', ctx.key, {'__loop_index_1': __loop_index_1, 'cart': cart, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = item, params = (reply_to,))

@user_operator.register
async def bulk_purchase_with_tiers_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cart, quantities, total_cost) = (params.get('__loop_index_1'), params.get('cart'), params.get('quantities'), params.get('total_cost'))
    __state__['balance'] -= total_cost
    ctx.put(__state__)
    return send_reply(ctx, reply_to, "Bulk purchase complete. Remaining balance: " + str(__state__['balance']))

@user_operator.register
async def bulk_purchase_with_tiers_step_4(ctx: StatefulFunction, func_context, attr_7 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cart, item, quantities, requested_amount, total_cost) = (params.get('__loop_index_1'), params.get('cart'), params.get('item'), params.get('quantities'), params.get('requested_amount'), params.get('total_cost'))

    if attr_7 >= requested_amount:
        current_item_cost = 0
        __loop_index_2 = 1
        ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_5', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'cart': cart, 'current_item_cost': current_item_cost, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost}, None, reply_to))
    else:
        logging.warning(f"Skipping {item} due to low stock.")
        ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cart': cart, 'quantities': quantities, 'total_cost': total_cost}, None, reply_to))

@user_operator.register
async def bulk_purchase_with_tiers_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, cart, current_item_cost, item, quantities, requested_amount, total_cost) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('cart'), params.get('current_item_cost'), params.get('item'), params.get('quantities'), params.get('requested_amount'), params.get('total_cost'))
    if __loop_index_2 >= requested_amount + 1:
        ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_6', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'cart': cart, 'current_item_cost': current_item_cost, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost}, None, reply_to))
    else:
        unit = __loop_index_2
        __loop_index_2 += 1
        if unit > 50:
            reply_to = push_continuation(ctx, reply_to, 'user', 'bulk_purchase_with_tiers_step_8', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'cart': cart, 'current_item_cost': current_item_cost, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost})
            ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))
        elif unit > 10:
            reply_to = push_continuation(ctx, reply_to, 'user', 'bulk_purchase_with_tiers_step_9', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'cart': cart, 'current_item_cost': current_item_cost, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost})
            ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))
        else:
            reply_to = push_continuation(ctx, reply_to, 'user', 'bulk_purchase_with_tiers_step_10', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'cart': cart, 'current_item_cost': current_item_cost, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost})
            ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))

@user_operator.register
async def bulk_purchase_with_tiers_step_6(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, cart, current_item_cost, item, quantities, requested_amount, total_cost) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('cart'), params.get('current_item_cost'), params.get('item'), params.get('quantities'), params.get('requested_amount'), params.get('total_cost'))

    if (total_cost + current_item_cost) > __state__['balance']:
        raise NotEnoughBalance("Cannot afford the entire cart.")
    reply_to = push_continuation(ctx, reply_to, 'user', 'bulk_purchase_with_tiers_step_7', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'cart': cart, 'current_item_cost': current_item_cost, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (-requested_amount, reply_to))

@user_operator.register
async def bulk_purchase_with_tiers_step_7(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, cart, current_item_cost, item, quantities, requested_amount, total_cost) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('cart'), params.get('current_item_cost'), params.get('item'), params.get('quantities'), params.get('requested_amount'), params.get('total_cost'))
    total_cost += current_item_cost

    for _ in range(requested_amount):
        __state__['myitems'].append(item)
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'cart': cart, 'quantities': quantities, 'total_cost': total_cost}, None, reply_to))

@user_operator.register
async def bulk_purchase_with_tiers_step_8(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, cart, current_item_cost, item, quantities, requested_amount, total_cost) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('cart'), params.get('current_item_cost'), params.get('item'), params.get('quantities'), params.get('requested_amount'), params.get('total_cost'))
    current_item_cost += int(attr_1 * 0.8)
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_5', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'cart': cart, 'current_item_cost': current_item_cost, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost}, None, reply_to))

@user_operator.register
async def bulk_purchase_with_tiers_step_9(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, cart, current_item_cost, item, quantities, requested_amount, total_cost) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('cart'), params.get('current_item_cost'), params.get('item'), params.get('quantities'), params.get('requested_amount'), params.get('total_cost'))
    current_item_cost += int(attr_2 * 0.9)
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_5', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'cart': cart, 'current_item_cost': current_item_cost, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost}, None, reply_to))

@user_operator.register
async def bulk_purchase_with_tiers_step_10(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, cart, current_item_cost, item, quantities, requested_amount, total_cost) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('cart'), params.get('current_item_cost'), params.get('item'), params.get('quantities'), params.get('requested_amount'), params.get('total_cost'))
    current_item_cost += attr_3
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_5', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'cart': cart, 'current_item_cost': current_item_cost, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost}, None, reply_to))


@user_operator.register
async def inventory_value(ctx: StatefulFunction, reply_to: list = None) -> int:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'user', function_name = 'inventory_value_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1}, None, reply_to))

@user_operator.register
async def inventory_value_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1) = (params.get('__loop_index_1'), params.get('_comp_result_1'))
    if __loop_index_1 >= len(__state__['myitems']):
        ctx.put(__state__)
        ctx.call_remote_async(operator_name = 'user', function_name = 'inventory_value_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1}, None, reply_to))
    else:
        item = __state__['myitems'][__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'user', 'inventory_value_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'item': item})
        ctx.put(__state__)
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))

@user_operator.register
async def inventory_value_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1) = (params.get('__loop_index_1'), params.get('_comp_result_1'))
    return send_reply(ctx, reply_to, sum(_comp_result_1))

@user_operator.register
async def inventory_value_step_4(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, item) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('item'))
    if attr_3 > 20:
        reply_to = push_continuation(ctx, reply_to, 'user', 'inventory_value_step_5', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))
    else:
        ctx.call_remote_async(operator_name = 'user', function_name = 'inventory_value_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1}, None, reply_to))

@user_operator.register
async def inventory_value_step_5(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1) = (params.get('__loop_index_1'), params.get('_comp_result_1'))
    _comp_result_1.append(attr_1)
    ctx.call_remote_async(operator_name = 'user', function_name = 'inventory_value_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1}, None, reply_to))


@user_operator.register
async def my_item_prices(ctx: StatefulFunction, reply_to: list = None) -> list[int]:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'user', function_name = 'my_item_prices_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1}, None, reply_to))

@user_operator.register
async def my_item_prices_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1) = (params.get('__loop_index_1'), params.get('_comp_result_1'))
    if __loop_index_1 >= len(__state__['myitems']):
        ctx.put(__state__)
        ctx.call_remote_async(operator_name = 'user', function_name = 'my_item_prices_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1}, None, reply_to))
    else:
        item = __state__['myitems'][__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'user', 'my_item_prices_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1})
        ctx.put(__state__)
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))

@user_operator.register
async def my_item_prices_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1) = (params.get('__loop_index_1'), params.get('_comp_result_1'))
    return send_reply(ctx, reply_to, _comp_result_1)

@user_operator.register
async def my_item_prices_step_4(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1) = (params.get('__loop_index_1'), params.get('_comp_result_1'))
    _comp_result_1.append(attr_1)
    ctx.call_remote_async(operator_name = 'user', function_name = 'my_item_prices_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1}, None, reply_to))


@user_operator.register
async def ret_tuple(ctx: StatefulFunction, item: str, reply_to: list = None) -> tuple[int, int]:
    reply_to = push_continuation(ctx, reply_to, 'user', 'ret_tuple_step_2', ctx.key, {'item': item})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))

@user_operator.register
async def ret_tuple_step_2(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (item,) = (params.get('item'),)
    reply_to = push_continuation(ctx, reply_to, 'user', 'ret_tuple_step_3', ctx.key, {'attr_1': attr_1})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = item, params = (reply_to,))

@user_operator.register
async def ret_tuple_step_3(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (attr_1,) = (params.get('attr_1'),)
    return send_reply(ctx, reply_to, (attr_1, attr_2))


@user_operator.register
async def ret_dict(ctx: StatefulFunction, item: str, reply_to: list = None) -> dict[str, int]:
    reply_to = push_continuation(ctx, reply_to, 'user', 'ret_dict_step_2', ctx.key, {})
    ctx.call_remote_async(operator_name = 'user', function_name = 'ret_tuple', key = ctx.key, params = (item, reply_to))

@user_operator.register
async def ret_dict_step_2(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    price, stock = attr_1
    return send_reply(ctx, reply_to, {"price": price, "stock": stock})


@user_operator.register
async def fire_and_forget(ctx: StatefulFunction, item: str, reply_to: list = None) -> None:
    ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (1, [{'sink': True}]))


@user_operator.register
async def demo(ctx: StatefulFunction, reply_to: list = None) -> str:
    __state__ = ctx.get() or {}
    for item in __state__['myitems']:
        for _ in range(100):
            ctx.put(__state__)
            ctx.call_remote_async(operator_name = 'user', function_name = 'helper', key = ctx.key, params = (item, [{'sink': True}]))
    ctx.put(__state__)
    return send_reply(ctx, reply_to, "demo complete")


@user_operator.register
async def helper(ctx: StatefulFunction, item: str, reply_to: list = None) -> int:
    reply_to = push_continuation(ctx, reply_to, 'user', 'helper_step_2', ctx.key, {})
    ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (1, reply_to))

@user_operator.register
async def helper_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    return send_reply(ctx, reply_to, 1)


@user_operator.register
async def demo2(ctx: StatefulFunction, item: Optional[str] = None, reply_to: list = None) -> str:
    if item is None:
        return send_reply(ctx, reply_to, "No item provided")
    reply_to = push_continuation(ctx, reply_to, 'user', 'demo2_step_2', ctx.key, {})
    ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (1, reply_to))

@user_operator.register
async def demo2_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    return send_reply(ctx, reply_to, "demo complete")


@user_operator.register
async def recursion_test(ctx: StatefulFunction, items: list[str], reply_to: list = None) -> int:
    if not items:
        return send_reply(ctx, reply_to, 0)
    attr_1 = items[0]
    reply_to = push_continuation(ctx, reply_to, 'user', 'recursion_test_step_2', ctx.key, {'items': items})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = attr_1, params = (reply_to,))

@user_operator.register
async def recursion_test_step_2(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (items,) = (params.get('items'),)
    reply_to = push_continuation(ctx, reply_to, 'user', 'recursion_test_step_3', ctx.key, {'attr_2': attr_2})
    ctx.call_remote_async(operator_name = 'user', function_name = 'recursion_test', key = ctx.key, params = (items[1:], reply_to))

@user_operator.register
async def recursion_test_step_3(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (attr_2,) = (params.get('attr_2'),)
    return send_reply(ctx, reply_to, attr_2 + attr_3)


@user_operator.register
async def comprehensions(ctx: StatefulFunction, items: list[str], reply_to: list = None) -> dict[str, int]:
    _comp_result_1 = {}
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'user', function_name = 'comprehensions_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'items': items}, None, reply_to))

@user_operator.register
async def comprehensions_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, items) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('items'))
    if __loop_index_1 >= len(items):
        ctx.call_remote_async(operator_name = 'user', function_name = 'comprehensions_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'items': items}, None, reply_to))
    else:
        item = items[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'user', 'comprehensions_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'item': item, 'items': items})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = item, params = (reply_to,))

@user_operator.register
async def comprehensions_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, items) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('items'))
    return send_reply(ctx, reply_to, _comp_result_1)

@user_operator.register
async def comprehensions_step_4(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, item, items) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('item'), params.get('items'))
    _comp_result_1[item] = attr_1
    ctx.call_remote_async(operator_name = 'user', function_name = 'comprehensions_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'items': items}, None, reply_to))


@user_operator.register
async def type_test(ctx: StatefulFunction, hard: list[list[dict[str, int]]], easy: list[list[str]], reply_to: list = None) -> str:
    temp = easy[0][0]
    reply_to = push_continuation(ctx, reply_to, 'user', 'type_test_step_2', ctx.key, {'hard': hard})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = temp, params = (reply_to,))

@user_operator.register
async def type_test_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (hard,) = (params.get('hard'),)
    attr_2 = hard[0][0]
    attr_3 = attr_2.keys()
    attr_4 = list(attr_3)[0]
    reply_to = push_continuation(ctx, reply_to, 'user', 'type_test_step_3', ctx.key, {})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = attr_4, params = (reply_to,))

@user_operator.register
async def type_test_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    temp4 = __state__['myitems'][0]
    reply_to = push_continuation(ctx, reply_to, 'user', 'type_test_step_4', ctx.key, {})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = temp4, params = (reply_to,))

@user_operator.register
async def type_test_step_4(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    attr_7 = __state__['myitems'][0]
    reply_to = push_continuation(ctx, reply_to, 'user', 'type_test_step_5', ctx.key, {})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = attr_7, params = (reply_to,))

@user_operator.register
async def type_test_step_5(ctx: StatefulFunction, func_context, stock_val = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    lst = [__state__['myitems'][0], __state__['myitems'][1]]
    attr_9 = lst[0]
    reply_to = push_continuation(ctx, reply_to, 'user', 'type_test_step_6', ctx.key, {})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = attr_9, params = (reply_to,))

@user_operator.register
async def type_test_step_6(ctx: StatefulFunction, func_context, stock = None, reply_to: list = None):
    return send_reply(ctx, reply_to, "hello")


@user_operator.register
async def process_cart_with_limits(ctx: StatefulFunction, cart: list[str], max_spend: int, reply_to: list = None) -> dict:
    purchased = {}
    total_spent = 0
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'user', function_name = 'process_cart_with_limits_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cart': cart, 'max_spend': max_spend, 'purchased': purchased, 'total_spent': total_spent}, None, reply_to))

@user_operator.register
async def process_cart_with_limits_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cart, max_spend, purchased, total_spent) = (params.get('__loop_index_1'), params.get('cart'), params.get('max_spend'), params.get('purchased'), params.get('total_spent'))
    if __loop_index_1 >= len(cart):
        ctx.call_remote_async(operator_name = 'user', function_name = 'process_cart_with_limits_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cart': cart, 'max_spend': max_spend, 'purchased': purchased, 'total_spent': total_spent}, None, reply_to))
    else:
        item = cart[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'user', 'process_cart_with_limits_step_4', ctx.key, {'__loop_index_1': __loop_index_1, 'cart': cart, 'item': item, 'max_spend': max_spend, 'purchased': purchased, 'total_spent': total_spent})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))

@user_operator.register
async def process_cart_with_limits_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cart, max_spend, purchased, total_spent) = (params.get('__loop_index_1'), params.get('cart'), params.get('max_spend'), params.get('purchased'), params.get('total_spent'))
    return send_reply(ctx, reply_to, purchased)

@user_operator.register
async def process_cart_with_limits_step_4(ctx: StatefulFunction, func_context, price = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cart, item, max_spend, purchased, total_spent) = (params.get('__loop_index_1'), params.get('cart'), params.get('item'), params.get('max_spend'), params.get('purchased'), params.get('total_spent'))

    if price > __state__['balance']:
        ctx.put(__state__)
        ctx.call_remote_async(operator_name = 'user', function_name = 'process_cart_with_limits_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cart': cart, 'max_spend': max_spend, 'purchased': purchased, 'total_spent': total_spent}, None, reply_to))
    else:

        if total_spent >= max_spend:
            ctx.put(__state__)
            ctx.call_remote_async(operator_name = 'user', function_name = 'process_cart_with_limits_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cart': cart, 'max_spend': max_spend, 'purchased': purchased, 'total_spent': total_spent}, None, reply_to))
        else:
            units_bought = 0
            ctx.put(__state__)
            ctx.call_remote_async(operator_name = 'user', function_name = 'process_cart_with_limits_step_5', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cart': cart, 'item': item, 'max_spend': max_spend, 'price': price, 'purchased': purchased, 'total_spent': total_spent, 'units_bought': units_bought}, None, reply_to))

@user_operator.register
async def process_cart_with_limits_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cart, item, max_spend, price, purchased, total_spent, units_bought) = (params.get('__loop_index_1'), params.get('cart'), params.get('item'), params.get('max_spend'), params.get('price'), params.get('purchased'), params.get('total_spent'), params.get('units_bought'))
    reply_to = push_continuation(ctx, reply_to, 'user', 'process_cart_with_limits_step_7', ctx.key, {'__loop_index_1': __loop_index_1, 'cart': cart, 'item': item, 'max_spend': max_spend, 'price': price, 'purchased': purchased, 'total_spent': total_spent, 'units_bought': units_bought})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = item, params = (reply_to,))

@user_operator.register
async def process_cart_with_limits_step_6(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cart, item, max_spend, price, purchased, total_spent, units_bought) = (params.get('__loop_index_1'), params.get('cart'), params.get('item'), params.get('max_spend'), params.get('price'), params.get('purchased'), params.get('total_spent'), params.get('units_bought'))

    if units_bought > 0:
        purchased[item] = units_bought
    ctx.call_remote_async(operator_name = 'user', function_name = 'process_cart_with_limits_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cart': cart, 'max_spend': max_spend, 'purchased': purchased, 'total_spent': total_spent}, None, reply_to))

@user_operator.register
async def process_cart_with_limits_step_7(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cart, item, max_spend, price, purchased, total_spent, units_bought) = (params.get('__loop_index_1'), params.get('cart'), params.get('item'), params.get('max_spend'), params.get('price'), params.get('purchased'), params.get('total_spent'), params.get('units_bought'))
    if not (0 < attr_3):
        ctx.put(__state__)
        ctx.call_remote_async(operator_name = 'user', function_name = 'process_cart_with_limits_step_6', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cart': cart, 'item': item, 'max_spend': max_spend, 'price': price, 'purchased': purchased, 'total_spent': total_spent, 'units_bought': units_bought}, None, reply_to))
    else:
        if total_spent + price > max_spend:
            ctx.put(__state__)
            ctx.call_remote_async(operator_name = 'user', function_name = 'process_cart_with_limits_step_6', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cart': cart, 'item': item, 'max_spend': max_spend, 'price': price, 'purchased': purchased, 'total_spent': total_spent, 'units_bought': units_bought}, None, reply_to))
        else:
            if price > __state__['balance']:
                ctx.put(__state__)
                ctx.call_remote_async(operator_name = 'user', function_name = 'process_cart_with_limits_step_6', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cart': cart, 'item': item, 'max_spend': max_spend, 'price': price, 'purchased': purchased, 'total_spent': total_spent, 'units_bought': units_bought}, None, reply_to))
            else:
                reply_to = push_continuation(ctx, reply_to, 'user', 'process_cart_with_limits_step_8', ctx.key, {'__loop_index_1': __loop_index_1, 'cart': cart, 'item': item, 'max_spend': max_spend, 'price': price, 'purchased': purchased, 'total_spent': total_spent, 'units_bought': units_bought})
                ctx.put(__state__)
                ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (-1, reply_to))

@user_operator.register
async def process_cart_with_limits_step_8(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cart, item, max_spend, price, purchased, total_spent, units_bought) = (params.get('__loop_index_1'), params.get('cart'), params.get('item'), params.get('max_spend'), params.get('price'), params.get('purchased'), params.get('total_spent'), params.get('units_bought'))
    __state__['balance'] -= price
    total_spent += price
    units_bought += 1
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'user', function_name = 'process_cart_with_limits_step_5', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cart': cart, 'item': item, 'max_spend': max_spend, 'price': price, 'purchased': purchased, 'total_spent': total_spent, 'units_bought': units_bought}, None, reply_to))


@user_operator.register
async def transfer_balance(ctx: StatefulFunction, recipient: 'User', amount: int, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if __state__['balance'] < amount:
        raise NotEnoughBalance("Insufficient balance for transfer.")
    __state__['balance'] -= amount
    reply_to = push_continuation(ctx, reply_to, 'user', 'transfer_balance_step_2', ctx.key, {})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'user', function_name = 'add_balance', key = recipient, params = (amount, reply_to))

@user_operator.register
async def transfer_balance_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    return send_reply(ctx, reply_to, True)


@user_operator.register
async def multi_restock(ctx: StatefulFunction, items: list[str], amounts: list[int], reply_to: list = None) -> int:
    total_added = 0
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'user', function_name = 'multi_restock_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'amounts': amounts, 'items': items, 'total_added': total_added}, None, reply_to))

@user_operator.register
async def multi_restock_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, amounts, items, total_added) = (params.get('__loop_index_1'), params.get('amounts'), params.get('items'), params.get('total_added'))
    if __loop_index_1 >= len(items):
        ctx.call_remote_async(operator_name = 'user', function_name = 'multi_restock_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'amounts': amounts, 'items': items, 'total_added': total_added}, None, reply_to))
    else:
        item = items[__loop_index_1]
        amount = amounts[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'user', 'multi_restock_step_4', ctx.key, {'__loop_index_1': __loop_index_1, 'amount': amount, 'amounts': amounts, 'items': items, 'total_added': total_added})
        ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (amount, reply_to))

@user_operator.register
async def multi_restock_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, amounts, items, total_added) = (params.get('__loop_index_1'), params.get('amounts'), params.get('items'), params.get('total_added'))
    return send_reply(ctx, reply_to, total_added)

@user_operator.register
async def multi_restock_step_4(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, amount, amounts, items, total_added) = (params.get('__loop_index_1'), params.get('amount'), params.get('amounts'), params.get('items'), params.get('total_added'))
    total_added += amount
    ctx.call_remote_async(operator_name = 'user', function_name = 'multi_restock_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'amounts': amounts, 'items': items, 'total_added': total_added}, None, reply_to))


@user_operator.register
async def most_valuable_item_price(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    if not __state__['myitems']:
        ctx.put(__state__)
        return send_reply(ctx, reply_to, 0)
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'user', function_name = 'most_valuable_item_price_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1}, None, reply_to))

@user_operator.register
async def most_valuable_item_price_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1) = (params.get('__loop_index_1'), params.get('_comp_result_1'))
    if __loop_index_1 >= len(__state__['myitems']):
        ctx.put(__state__)
        ctx.call_remote_async(operator_name = 'user', function_name = 'most_valuable_item_price_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1}, None, reply_to))
    else:
        item = __state__['myitems'][__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'user', 'most_valuable_item_price_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1})
        ctx.put(__state__)
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))

@user_operator.register
async def most_valuable_item_price_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1) = (params.get('__loop_index_1'), params.get('_comp_result_1'))
    return send_reply(ctx, reply_to, max(_comp_result_1))

@user_operator.register
async def most_valuable_item_price_step_4(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1) = (params.get('__loop_index_1'), params.get('_comp_result_1'))
    _comp_result_1.append(attr_1)
    ctx.call_remote_async(operator_name = 'user', function_name = 'most_valuable_item_price_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1}, None, reply_to))


@user_operator.register
async def can_afford_cart(ctx: StatefulFunction, items: list[str], reply_to: list = None) -> bool:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'user', function_name = 'can_afford_cart_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'items': items}, None, reply_to))

@user_operator.register
async def can_afford_cart_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, items) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('items'))
    if __loop_index_1 >= len(items):
        ctx.call_remote_async(operator_name = 'user', function_name = 'can_afford_cart_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'items': items}, None, reply_to))
    else:
        item = items[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'user', 'can_afford_cart_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'items': items})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))

@user_operator.register
async def can_afford_cart_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, items) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('items'))
    total = sum(_comp_result_1)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['balance'] >= total)

@user_operator.register
async def can_afford_cart_step_4(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, items) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('items'))
    _comp_result_1.append(attr_1)
    ctx.call_remote_async(operator_name = 'user', function_name = 'can_afford_cart_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'items': items}, None, reply_to))


@user_operator.register
async def group_items_by_price_bucket(ctx: StatefulFunction, items: list[str], reply_to: list = None) -> dict:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'user', function_name = 'group_items_by_price_bucket_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'items': items}, None, reply_to))

@user_operator.register
async def group_items_by_price_bucket_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, items) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('items'))
    if __loop_index_1 >= len(items):
        ctx.call_remote_async(operator_name = 'user', function_name = 'group_items_by_price_bucket_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'items': items}, None, reply_to))
    else:
        item = items[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'user', 'group_items_by_price_bucket_step_12', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'item': item, 'items': items})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))

@user_operator.register
async def group_items_by_price_bucket_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, items) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('items'))
    _comp_result_2 = []
    __loop_index_2 = 0
    ctx.call_remote_async(operator_name = 'user', function_name = 'group_items_by_price_bucket_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'items': items}, None, reply_to))

@user_operator.register
async def group_items_by_price_bucket_step_4(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, _comp_result_1, _comp_result_2, items) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('items'))
    if __loop_index_2 >= len(items):
        ctx.call_remote_async(operator_name = 'user', function_name = 'group_items_by_price_bucket_step_5', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'items': items}, None, reply_to))
    else:
        item = items[__loop_index_2]
        __loop_index_2 += 1
        reply_to = push_continuation(ctx, reply_to, 'user', 'group_items_by_price_bucket_step_10', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'item': item, 'items': items})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))

@user_operator.register
async def group_items_by_price_bucket_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, _comp_result_1, _comp_result_2, items) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('items'))
    _comp_result_3 = []
    __loop_index_3 = 0
    ctx.call_remote_async(operator_name = 'user', function_name = 'group_items_by_price_bucket_step_6', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, 'items': items}, None, reply_to))

@user_operator.register
async def group_items_by_price_bucket_step_6(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, __loop_index_3, _comp_result_1, _comp_result_2, _comp_result_3, items) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('__loop_index_3'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('_comp_result_3'), params.get('items'))
    if __loop_index_3 >= len(items):
        ctx.call_remote_async(operator_name = 'user', function_name = 'group_items_by_price_bucket_step_7', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, 'items': items}, None, reply_to))
    else:
        item = items[__loop_index_3]
        __loop_index_3 += 1
        reply_to = push_continuation(ctx, reply_to, 'user', 'group_items_by_price_bucket_step_8', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, 'item': item, 'items': items})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))

@user_operator.register
async def group_items_by_price_bucket_step_7(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, __loop_index_3, _comp_result_1, _comp_result_2, _comp_result_3, items) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('__loop_index_3'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('_comp_result_3'), params.get('items'))
    return send_reply(ctx, reply_to, {
        'cheap': _comp_result_1,
        'mid': _comp_result_2,
        'expensive': _comp_result_3,
    })

@user_operator.register
async def group_items_by_price_bucket_step_8(ctx: StatefulFunction, func_context, attr_9 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, __loop_index_3, _comp_result_1, _comp_result_2, _comp_result_3, item, items) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('__loop_index_3'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('_comp_result_3'), params.get('item'), params.get('items'))
    if attr_9 > 100:
        reply_to = push_continuation(ctx, reply_to, 'user', 'group_items_by_price_bucket_step_9', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, 'items': items})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))
    else:
        ctx.call_remote_async(operator_name = 'user', function_name = 'group_items_by_price_bucket_step_6', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, 'items': items}, None, reply_to))

@user_operator.register
async def group_items_by_price_bucket_step_9(ctx: StatefulFunction, func_context, attr_7 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, __loop_index_3, _comp_result_1, _comp_result_2, _comp_result_3, items) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('__loop_index_3'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('_comp_result_3'), params.get('items'))
    _comp_result_3.append(attr_7)
    ctx.call_remote_async(operator_name = 'user', function_name = 'group_items_by_price_bucket_step_6', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, 'items': items}, None, reply_to))

@user_operator.register
async def group_items_by_price_bucket_step_10(ctx: StatefulFunction, func_context, attr_6 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, _comp_result_1, _comp_result_2, item, items) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('item'), params.get('items'))
    if 20 <= attr_6 <= 100:
        reply_to = push_continuation(ctx, reply_to, 'user', 'group_items_by_price_bucket_step_11', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'items': items})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))
    else:
        ctx.call_remote_async(operator_name = 'user', function_name = 'group_items_by_price_bucket_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'items': items}, None, reply_to))

@user_operator.register
async def group_items_by_price_bucket_step_11(ctx: StatefulFunction, func_context, attr_4 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, _comp_result_1, _comp_result_2, items) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('items'))
    _comp_result_2.append(attr_4)
    ctx.call_remote_async(operator_name = 'user', function_name = 'group_items_by_price_bucket_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'items': items}, None, reply_to))

@user_operator.register
async def group_items_by_price_bucket_step_12(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, item, items) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('item'), params.get('items'))
    if attr_3 < 20:
        reply_to = push_continuation(ctx, reply_to, 'user', 'group_items_by_price_bucket_step_13', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'items': items})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))
    else:
        ctx.call_remote_async(operator_name = 'user', function_name = 'group_items_by_price_bucket_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'items': items}, None, reply_to))

@user_operator.register
async def group_items_by_price_bucket_step_13(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, items) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('items'))
    _comp_result_1.append(attr_1)
    ctx.call_remote_async(operator_name = 'user', function_name = 'group_items_by_price_bucket_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'items': items}, None, reply_to))


@user_operator.register
async def is_in_stock(ctx: StatefulFunction, item: str, reply_to: list = None) -> bool:
    _sc_1 = item is not None
    if _sc_1:
        reply_to = push_continuation(ctx, reply_to, 'user', 'is_in_stock_step_2', ctx.key, {})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = item, params = (reply_to,))
    else:
        return send_reply(ctx, reply_to, _sc_1)

@user_operator.register
async def is_in_stock_step_2(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    _sc_1 = attr_1 > 0
    return send_reply(ctx, reply_to, _sc_1)




@user_operator.register
async def get_discounted_price(ctx: StatefulFunction, item: str, coupon: str, reply_to: list = None) -> int:
    _gather_id = init_gather_barrier(ctx, 2, {}, reply_to)
    _g_reply_0 = [{'op_name': 'user', 'fun': 'get_discounted_price_step_2', 'id': ctx.key, 'context': {'_g_barrier': _gather_id, '_g_tag': 0}}]
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (_g_reply_0,))
    _g_reply_1 = [{'op_name': 'user', 'fun': 'get_discounted_price_step_2', 'id': ctx.key, 'context': {'_g_barrier': _gather_id, '_g_tag': 1}}]
    ctx.call_remote_async(operator_name = 'coupon', function_name = 'get_discount', key = coupon, params = (_g_reply_1,))

@user_operator.register
async def get_discounted_price_step_2(ctx: StatefulFunction, func_context, _gather_partial = None, reply_to: list = None):
    barrier_id = func_context['_g_barrier']
    _g_tag = func_context['_g_tag']
    (is_complete, _g_results, saved, parent_reply_to) = update_gather_barrier(ctx, barrier_id, _g_tag, _gather_partial)
    if not is_complete:
        return
    reply_to = parent_reply_to
    price, discount = _g_results
    discounted_price = price - discount
    return send_reply(ctx, reply_to, max(discounted_price, 0))


@user_operator.register
async def buy_with_coupon(ctx: StatefulFunction, item: str, coupon: Optional[str], reply_to: list = None) -> bool:
    if coupon is None:
        reply_to = push_continuation(ctx, reply_to, 'user', 'buy_with_coupon_step_2', ctx.key, {})
        ctx.call_remote_async(operator_name = 'user', function_name = 'buy_item', key = ctx.key, params = (1, item, reply_to))
    else:
        reply_to = push_continuation(ctx, reply_to, 'user', 'buy_with_coupon_step_3', ctx.key, {'item': item})
        ctx.call_remote_async(operator_name = 'user', function_name = 'get_discounted_price', key = ctx.key, params = (item, coupon, reply_to))

@user_operator.register
async def buy_with_coupon_step_2(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    return send_reply(ctx, reply_to, attr_1)

@user_operator.register
async def buy_with_coupon_step_3(ctx: StatefulFunction, func_context, discounted_price = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (item,) = (params.get('item'),)

    if __state__['balance'] < discounted_price:
        raise NotEnoughBalance("Not enough balance to buy the item with coupon.")
    reply_to = push_continuation(ctx, reply_to, 'user', 'buy_with_coupon_step_4', ctx.key, {'discounted_price': discounted_price, 'item': item})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'user', function_name = 'is_in_stock', key = ctx.key, params = (item, reply_to))

@user_operator.register
async def buy_with_coupon_step_4(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (discounted_price, item) = (params.get('discounted_price'), params.get('item'))
    if not attr_3:
        raise OutOfStock("Item is out of stock.")
    reply_to = push_continuation(ctx, reply_to, 'user', 'buy_with_coupon_step_5', ctx.key, {'discounted_price': discounted_price, 'item': item})
    ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (-1, reply_to))

@user_operator.register
async def buy_with_coupon_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (discounted_price, item) = (params.get('discounted_price'), params.get('item'))
    __state__['balance'] -= discounted_price
    __state__['myitems'].append(item)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)



@user_operator.register
async def gather_in_loop(ctx: StatefulFunction, items: list[str], coupons: list[str], reply_to: list = None) -> int:
    total = 0
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'user', function_name = 'gather_in_loop_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'coupons': coupons, 'items': items, 'total': total}, None, reply_to))

@user_operator.register
async def gather_in_loop_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, coupons, items, total) = (params.get('__loop_index_1'), params.get('coupons'), params.get('items'), params.get('total'))
    if __loop_index_1 >= len(items):
        ctx.call_remote_async(operator_name = 'user', function_name = 'gather_in_loop_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'coupons': coupons, 'items': items, 'total': total}, None, reply_to))
    else:
        item = items[__loop_index_1]
        coupon = coupons[__loop_index_1]
        __loop_index_1 += 1
        _gather_id = init_gather_barrier(ctx, 2, {'__loop_index_1': __loop_index_1, 'coupons': coupons, 'items': items, 'total': total}, reply_to)
        _g_reply_0 = [{'op_name': 'user', 'fun': 'gather_in_loop_step_4', 'id': ctx.key, 'context': {'_g_barrier': _gather_id, '_g_tag': 0}}]
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (_g_reply_0,))
        _g_reply_1 = [{'op_name': 'user', 'fun': 'gather_in_loop_step_4', 'id': ctx.key, 'context': {'_g_barrier': _gather_id, '_g_tag': 1}}]
        ctx.call_remote_async(operator_name = 'coupon', function_name = 'get_discount', key = coupon, params = (_g_reply_1,))

@user_operator.register
async def gather_in_loop_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, coupons, items, total) = (params.get('__loop_index_1'), params.get('coupons'), params.get('items'), params.get('total'))
    return send_reply(ctx, reply_to, total)

@user_operator.register
async def gather_in_loop_step_4(ctx: StatefulFunction, func_context, _gather_partial = None, reply_to: list = None):
    barrier_id = func_context['_g_barrier']
    _g_tag = func_context['_g_tag']
    (is_complete, _g_results, saved, parent_reply_to) = update_gather_barrier(ctx, barrier_id, _g_tag, _gather_partial)
    if not is_complete:
        return
    (__loop_index_1, coupons, items, total) = (saved.get('__loop_index_1'), saved.get('coupons'), saved.get('items'), saved.get('total'))
    reply_to = parent_reply_to
    price, discount = _g_results
    discounted_price = price - discount
    total += discounted_price
    ctx.call_remote_async(operator_name = 'user', function_name = 'gather_in_loop_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'coupons': coupons, 'items': items, 'total': total}, None, reply_to))


@user_operator.register
async def inventory_value_gather(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    _g_iter = list(__state__['myitems'])
    _gather_id = init_gather_barrier(ctx, len(_g_iter), {}, reply_to)
    for (_g_tag, item) in enumerate(_g_iter):
        _g_reply = [{'op_name': 'user', 'fun': 'inventory_value_gather_step_2', 'id': ctx.key, 'context': {'_g_barrier': _gather_id, '_g_tag': _g_tag}}]
        ctx.put(__state__)
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (_g_reply,))
    ctx.put(__state__)

@user_operator.register
async def inventory_value_gather_step_2(ctx: StatefulFunction, func_context, _gather_partial = None, reply_to: list = None):
    barrier_id = func_context['_g_barrier']
    _g_tag = func_context['_g_tag']
    (is_complete, _g_results, saved, parent_reply_to) = update_gather_barrier(ctx, barrier_id, _g_tag, _gather_partial)
    if not is_complete:
        return
    reply_to = parent_reply_to
    prices = _g_results
    return send_reply(ctx, reply_to, sum(list(prices)))



@user_operator.register
async def test(ctx: StatefulFunction, item: str, reply_to: list = None) -> int:
    flag = False
    reply_to = push_continuation(ctx, reply_to, 'user', 'test_step_2', ctx.key, {'flag': flag})
    ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (1, reply_to))

@user_operator.register
async def test_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (flag,) = (params.get('flag'),)

    if 1 == 1:
        flag = True
    else:
        temp = 5

    if flag:
        return send_reply(ctx, reply_to, 1)
    return send_reply(ctx, reply_to, temp)
