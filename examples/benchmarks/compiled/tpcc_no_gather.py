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

from __future__ import annotations
from typing import Any, Dict, Optional
import datetime


# ──────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────

class InsufficientStock(Exception):
    pass

class InvalidItem(Exception):
    pass

class WHDoesNotExist(Exception):
    pass

class DistrictDoesNotExist(Exception):
    pass

class TPCCException(Exception):
    pass

class CustomerDoesNotExist(Exception):
    pass

class HistoryDoesNotExist(Exception):
    pass

class StockDoesNotExist(Exception):
    pass

class OrderDoesNotExist(Exception):
    pass

class OrderLineDoesNotExist(Exception):
    pass
warehouse_operator = Operator('warehouse', n_partitions=4)

@warehouse_operator.register
async def insert(ctx: StatefulFunction, w_id: int, W_NAME: str, W_STREET_1: str, W_STREET_2: str,
             W_CITY: str, W_STATE: str, W_ZIP: str, W_TAX: float, W_YTD: float, reply_to: list = None):
    __state__ = {}
    __state__['w_id'] = w_id
    __state__['W_NAME'] = W_NAME
    __state__['W_STREET_1'] = W_STREET_1
    __state__['W_STREET_2'] = W_STREET_2
    __state__['W_CITY'] = W_CITY
    __state__['W_STATE'] = W_STATE
    __state__['W_ZIP'] = W_ZIP
    __state__['W_TAX'] = W_TAX
    __state__['W_YTD'] = W_YTD
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@warehouse_operator.register
async def get_warehouse(ctx: StatefulFunction, reply_to: list = None) -> Dict:
    __state__ = ctx.get() or {}
    if not bool(__state__):
        raise WHDoesNotExist(f"Warehouse with key: {ctx.key} does not exist.")
    data = {
        'W_NAME': __state__['W_NAME'], 'W_TAX': __state__['W_TAX'], 'W_YTD': __state__['W_YTD'],
        'W_STREET_1': __state__['W_STREET_1'], 'W_STREET_2': __state__['W_STREET_2'],
        'W_CITY': __state__['W_CITY'], 'W_STATE': __state__['W_STATE'], 'W_ZIP': __state__['W_ZIP'],
    }
    ctx.put(__state__)
    return send_reply(ctx, reply_to, data)


@warehouse_operator.register
async def pay(ctx: StatefulFunction, h_amount: float, reply_to: list = None) -> Dict:
    __state__ = ctx.get() or {}
    if not bool(__state__):
        raise WHDoesNotExist(f"Warehouse with key: {ctx.key} does not exist")
    __state__['W_YTD'] = float(__state__['W_YTD']) + h_amount
    data = {
        'W_NAME': __state__['W_NAME'], 'W_TAX': __state__['W_TAX'], 'W_YTD': __state__['W_YTD'],
        'W_STREET_1': __state__['W_STREET_1'], 'W_STREET_2': __state__['W_STREET_2'],
        'W_CITY': __state__['W_CITY'], 'W_STATE': __state__['W_STATE'], 'W_ZIP': __state__['W_ZIP'],
    }
    ctx.put(__state__)
    return send_reply(ctx, reply_to, data)

district_operator = Operator('district', n_partitions=4, composite_key_hash_params=(0, ':'))

@district_operator.register
async def insert(ctx: StatefulFunction, D_ID: int, D_W_ID: int, D_NAME: str, D_STREET_1: str, D_STREET_2: str,
             D_CITY: str, D_STATE: str, D_ZIP: str, D_TAX: float, D_YTD: float,
             D_NEXT_O_ID: int, reply_to: list = None):
    __state__ = {}
    __state__['D_ID'] = D_ID
    __state__['D_W_ID'] = D_W_ID
    __state__['D_NAME'] = D_NAME
    __state__['D_STREET_1'] = D_STREET_1
    __state__['D_STREET_2'] = D_STREET_2
    __state__['D_CITY'] = D_CITY
    __state__['D_STATE'] = D_STATE
    __state__['D_ZIP'] = D_ZIP
    __state__['D_TAX'] = D_TAX
    __state__['D_YTD'] = D_YTD
    __state__['D_NEXT_O_ID'] = D_NEXT_O_ID
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@district_operator.register
async def get_district(ctx: StatefulFunction, w_id: int, d_id: int, c_id: int,
                 o_entry_d: str, i_ids: list[int], i_qtys: list[int],
                 i_w_ids: list[int], all_local: bool, reply_to: list = None) -> Dict:
    __state__ = ctx.get() or {}
    if not bool(__state__):
        raise DistrictDoesNotExist(f"District with key: {ctx.key} does not exist")
    d_next_o_id = __state__['D_NEXT_O_ID']
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'order', function_name = 'insert', key = str(w_id) + ":" + str(d_id) + ":" + str(d_next_o_id), params = (w_id, d_id, d_next_o_id, c_id, o_entry_d, None, len(i_ids), all_local, [{'sink': True}]))
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'neworder', function_name = 'insert', key = str(w_id) + ":" + str(d_id) + ":" + str(d_next_o_id), params = (w_id, d_id, d_next_o_id, [{'sink': True}]))
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'district', function_name = 'get_district_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'd_id': d_id, 'd_next_o_id': d_next_o_id, 'i_ids': i_ids, 'i_qtys': i_qtys, 'i_w_ids': i_w_ids, 'o_entry_d': o_entry_d, 'w_id': w_id}, None, reply_to))

@district_operator.register
async def get_district_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, d_id, d_next_o_id, i_ids, i_qtys, i_w_ids, o_entry_d, w_id) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('d_id'), params.get('d_next_o_id'), params.get('i_ids'), params.get('i_qtys'), params.get('i_w_ids'), params.get('o_entry_d'), params.get('w_id'))
    if __loop_index_1 >= len(i_ids):
        ctx.call_remote_async(operator_name = 'district', function_name = 'get_district_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'd_id': d_id, 'd_next_o_id': d_next_o_id, 'i_ids': i_ids, 'i_qtys': i_qtys, 'i_w_ids': i_w_ids, 'o_entry_d': o_entry_d, 'w_id': w_id}, None, reply_to))
    else:
        i = __loop_index_1
        __loop_index_1 += 1
        attr_1 = i_ids[i]
        reply_to = push_continuation(ctx, reply_to, 'district', 'get_district_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'd_id': d_id, 'd_next_o_id': d_next_o_id, 'i_ids': i_ids, 'i_qtys': i_qtys, 'i_w_ids': i_w_ids, 'o_entry_d': o_entry_d, 'w_id': w_id})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_item', key = attr_1, params = (i, w_id, d_id, o_entry_d, i_qtys[i], i_w_ids[i], d_next_o_id, reply_to))

@district_operator.register
async def get_district_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, d_id, d_next_o_id, i_ids, i_qtys, i_w_ids, o_entry_d, w_id) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('d_id'), params.get('d_next_o_id'), params.get('i_ids'), params.get('i_qtys'), params.get('i_w_ids'), params.get('o_entry_d'), params.get('w_id'))
    item_replies = _comp_result_1
    __state__['D_NEXT_O_ID'] += 1
    data = {
        'D_ID': __state__['D_ID'], 'D_W_ID': __state__['D_W_ID'], 'D_NAME': __state__['D_NAME'],
        'D_TAX': __state__['D_TAX'], 'D_YTD': __state__['D_YTD'], 'D_NEXT_O_ID': __state__['D_NEXT_O_ID'],
        'D_STREET_1': __state__['D_STREET_1'], 'D_STREET_2': __state__['D_STREET_2'],
        'D_CITY': __state__['D_CITY'], 'D_STATE': __state__['D_STATE'], 'D_ZIP': __state__['D_ZIP'],
    }
    ctx.put(__state__)
    return send_reply(ctx, reply_to, {'district': data, 'items': item_replies})

@district_operator.register
async def get_district_step_4(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, d_id, d_next_o_id, i_ids, i_qtys, i_w_ids, o_entry_d, w_id) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('d_id'), params.get('d_next_o_id'), params.get('i_ids'), params.get('i_qtys'), params.get('i_w_ids'), params.get('o_entry_d'), params.get('w_id'))
    _comp_result_1.append(attr_2)
    ctx.call_remote_async(operator_name = 'district', function_name = 'get_district_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'd_id': d_id, 'd_next_o_id': d_next_o_id, 'i_ids': i_ids, 'i_qtys': i_qtys, 'i_w_ids': i_w_ids, 'o_entry_d': o_entry_d, 'w_id': w_id}, None, reply_to))


@district_operator.register
async def pay(ctx: StatefulFunction, h_amount: float, reply_to: list = None) -> Dict:
    __state__ = ctx.get() or {}
    if not bool(__state__):
        raise DistrictDoesNotExist(f"District with key: {ctx.key} does not exist")
    __state__['D_YTD'] = float(__state__['D_YTD']) + h_amount
    data = {
        'D_ID': __state__['D_ID'], 'D_W_ID': __state__['D_W_ID'], 'D_NAME': __state__['D_NAME'],
        'D_TAX': __state__['D_TAX'], 'D_YTD': __state__['D_YTD'], 'D_NEXT_O_ID': __state__['D_NEXT_O_ID'],
        'D_STREET_1': __state__['D_STREET_1'], 'D_STREET_2': __state__['D_STREET_2'],
        'D_CITY': __state__['D_CITY'], 'D_STATE': __state__['D_STATE'], 'D_ZIP': __state__['D_ZIP'],
    }
    ctx.put(__state__)
    return send_reply(ctx, reply_to, data)

item_operator = Operator('item', n_partitions=4)

@item_operator.register
async def insert(ctx: StatefulFunction, I_ID: int, I_IM_ID: int, I_NAME: str, I_PRICE: float, I_DATA: str, reply_to: list = None):
    __state__ = {}
    __state__['I_ID'] = I_ID
    __state__['I_IM_ID'] = I_IM_ID
    __state__['I_NAME'] = I_NAME
    __state__['I_PRICE'] = I_PRICE
    __state__['I_DATA'] = I_DATA
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@item_operator.register
async def get_item(ctx: StatefulFunction, index: int, w_id: int, d_id: int,
             o_entry_d: str, i_qty: int, i_w_id: int, d_next_o_id: int, reply_to: list = None) -> Dict:
    __state__ = ctx.get() or {}

    if not bool(__state__):
        raise TPCCException("Item number is not valid")
    attr_1 = __state__['I_DATA'].find("original")
    i_brand_generic = attr_1 != -1
    stock = f"{i_w_id}:{ctx.key}"
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'stock', function_name = 'update_stock', key = stock, params = (index, d_next_o_id, ctx.key, w_id, d_id, i_w_id, o_entry_d, i_qty, __state__['I_NAME'], __state__['I_PRICE'], i_brand_generic, reply_to))

customer_operator = Operator('customer', n_partitions=4, composite_key_hash_params=(0, ':'))

@customer_operator.register
async def insert(ctx: StatefulFunction, C_ID: int, C_D_ID: int, C_W_ID: int, C_FIRST: str, C_MIDDLE: str,
             C_LAST: str, C_STREET_1: str, C_STREET_2: str, C_CITY: str, C_STATE: str,
             C_ZIP: str, C_PHONE: str, C_SINCE: str, C_CREDIT: str,
             C_CREDIT_LIM: float, C_DISCOUNT: float, C_BALANCE: float,
             C_YTD_PAYMENT: float, C_PAYMENT_CNT: int, C_DELIVERY_CNT: int, C_DATA: str, reply_to: list = None):
    __state__ = {}
    __state__['C_ID'] = C_ID
    __state__['C_D_ID'] = C_D_ID
    __state__['C_W_ID'] = C_W_ID
    __state__['C_FIRST'] = C_FIRST
    __state__['C_MIDDLE'] = C_MIDDLE
    __state__['C_LAST'] = C_LAST
    __state__['C_STREET_1'] = C_STREET_1
    __state__['C_STREET_2'] = C_STREET_2
    __state__['C_CITY'] = C_CITY
    __state__['C_STATE'] = C_STATE
    __state__['C_ZIP'] = C_ZIP
    __state__['C_PHONE'] = C_PHONE
    __state__['C_SINCE'] = C_SINCE
    __state__['C_CREDIT'] = C_CREDIT
    __state__['C_CREDIT_LIM'] = C_CREDIT_LIM
    __state__['C_DISCOUNT'] = C_DISCOUNT
    __state__['C_BALANCE'] = C_BALANCE
    __state__['C_YTD_PAYMENT'] = C_YTD_PAYMENT
    __state__['C_PAYMENT_CNT'] = C_PAYMENT_CNT
    __state__['C_DELIVERY_CNT'] = C_DELIVERY_CNT
    __state__['C_DATA'] = C_DATA
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@customer_operator.register
async def get_customer(ctx: StatefulFunction, reply_to: list = None) -> Dict:
    __state__ = ctx.get() or {}
    if not bool(__state__):
        raise CustomerDoesNotExist(f"Customer with id: {ctx.key} does not exist")
    data = {
        'C_ID': __state__['C_ID'], 'C_D_ID': __state__['C_D_ID'], 'C_W_ID': __state__['C_W_ID'],
        'C_FIRST': __state__['C_FIRST'], 'C_MIDDLE': __state__['C_MIDDLE'], 'C_LAST': __state__['C_LAST'],
        'C_STREET_1': __state__['C_STREET_1'], 'C_STREET_2': __state__['C_STREET_2'],
        'C_CITY': __state__['C_CITY'], 'C_STATE': __state__['C_STATE'], 'C_ZIP': __state__['C_ZIP'],
        'C_PHONE': __state__['C_PHONE'], 'C_SINCE': __state__['C_SINCE'], 'C_CREDIT': __state__['C_CREDIT'],
        'C_CREDIT_LIM': __state__['C_CREDIT_LIM'], 'C_DISCOUNT': __state__['C_DISCOUNT'],
        'C_BALANCE': __state__['C_BALANCE'], 'C_YTD_PAYMENT': __state__['C_YTD_PAYMENT'],
        'C_PAYMENT_CNT': __state__['C_PAYMENT_CNT'], 'C_DELIVERY_CNT': __state__['C_DELIVERY_CNT'],
        'C_DATA': __state__['C_DATA'],
    }
    ctx.put(__state__)
    return send_reply(ctx, reply_to, data)


@customer_operator.register
async def pay(ctx: StatefulFunction, h_amount: float, d_id: int, w_id: int, reply_to: list = None) -> Dict:
    __state__ = ctx.get() or {}
    if not bool(__state__):
        raise CustomerDoesNotExist(f"Customer with id: {ctx.key} does not exist")
    __state__['C_BALANCE'] = float(__state__['C_BALANCE']) - h_amount
    __state__['C_YTD_PAYMENT'] = float(__state__['C_YTD_PAYMENT']) + h_amount
    __state__['C_PAYMENT_CNT'] = int(__state__['C_PAYMENT_CNT']) + 1

    if __state__['C_CREDIT'] == "BC":
        new_data = f"{__state__['C_ID']} {__state__['C_D_ID']} {__state__['C_W_ID']} {d_id} {w_id} {h_amount}"
        __state__['C_DATA'] = (new_data + "|" + __state__['C_DATA'])

        if len(__state__['C_DATA']) > 500:
            __state__['C_DATA'] = __state__['C_DATA'][:500]
    data = {
        'C_ID': __state__['C_ID'], 'C_D_ID': __state__['C_D_ID'], 'C_W_ID': __state__['C_W_ID'],
        'C_FIRST': __state__['C_FIRST'], 'C_MIDDLE': __state__['C_MIDDLE'], 'C_LAST': __state__['C_LAST'],
        'C_STREET_1': __state__['C_STREET_1'], 'C_STREET_2': __state__['C_STREET_2'],
        'C_CITY': __state__['C_CITY'], 'C_STATE': __state__['C_STATE'], 'C_ZIP': __state__['C_ZIP'],
        'C_PHONE': __state__['C_PHONE'], 'C_SINCE': __state__['C_SINCE'], 'C_CREDIT': __state__['C_CREDIT'],
        'C_CREDIT_LIM': __state__['C_CREDIT_LIM'], 'C_DISCOUNT': __state__['C_DISCOUNT'],
        'C_BALANCE': __state__['C_BALANCE'], 'C_YTD_PAYMENT': __state__['C_YTD_PAYMENT'],
        'C_PAYMENT_CNT': __state__['C_PAYMENT_CNT'], 'C_DELIVERY_CNT': __state__['C_DELIVERY_CNT'],
        'C_DATA': __state__['C_DATA'],
    }
    ctx.put(__state__)
    return send_reply(ctx, reply_to, data)

customerindex_operator = Operator('customerindex', n_partitions=4, composite_key_hash_params=(0, ':'))

@customerindex_operator.register
async def insert(ctx: StatefulFunction, C_W_ID: int, C_D_ID: int, C_LAST: str, customers: list[str], reply_to: list = None):
    __state__ = {}
    __state__['C_W_ID'] = C_W_ID
    __state__['C_D_ID'] = C_D_ID
    __state__['C_LAST'] = C_LAST
    __state__['customers'] = customers
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@customerindex_operator.register
async def pay(ctx: StatefulFunction, h_amount: float, d_id: int, w_id: int, reply_to: list = None) -> Dict:
    __state__ = ctx.get() or {}
    index = (len(__state__['customers']) - 1) // 2
    customer = __state__['customers'][index]
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'customer', function_name = 'pay', key = customer, params = (h_amount, d_id, w_id, reply_to))

stock_operator = Operator('stock', n_partitions=4, composite_key_hash_params=(0, ':'))

@stock_operator.register
async def insert(ctx: StatefulFunction, S_I_ID: int, S_W_ID: int, S_QUANTITY: int,
             S_DIST_01: str, S_DIST_02: str, S_DIST_03: str, S_DIST_04: str,
             S_DIST_05: str, S_DIST_06: str, S_DIST_07: str, S_DIST_08: str,
             S_DIST_09: str, S_DIST_10: str, S_YTD: int, S_ORDER_CNT: int,
             S_REMOTE_CNT: int, S_DATA: str, reply_to: list = None):
    __state__ = {}
    __state__['S_I_ID'] = S_I_ID
    __state__['S_W_ID'] = S_W_ID
    __state__['S_QUANTITY'] = S_QUANTITY
    __state__['S_DIST_01'] = S_DIST_01
    __state__['S_DIST_02'] = S_DIST_02
    __state__['S_DIST_03'] = S_DIST_03
    __state__['S_DIST_04'] = S_DIST_04
    __state__['S_DIST_05'] = S_DIST_05
    __state__['S_DIST_06'] = S_DIST_06
    __state__['S_DIST_07'] = S_DIST_07
    __state__['S_DIST_08'] = S_DIST_08
    __state__['S_DIST_09'] = S_DIST_09
    __state__['S_DIST_10'] = S_DIST_10
    __state__['S_YTD'] = S_YTD
    __state__['S_ORDER_CNT'] = S_ORDER_CNT
    __state__['S_REMOTE_CNT'] = S_REMOTE_CNT
    __state__['S_DATA'] = S_DATA
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@stock_operator.register
async def get_stock(ctx: StatefulFunction, reply_to: list = None) -> dict:
    __state__ = ctx.get() or {}
    data = {
        'S_I_ID': __state__['S_I_ID'].I_ID, 'S_W_ID': __state__['S_W_ID'], 'S_QUANTITY': __state__['S_QUANTITY'],
        'S_DIST_01': __state__['S_DIST_01'], 'S_DIST_02': __state__['S_DIST_02'], 'S_DIST_03': __state__['S_DIST_03'],
        'S_DIST_04': __state__['S_DIST_04'], 'S_DIST_05': __state__['S_DIST_05'], 'S_DIST_06': __state__['S_DIST_06'],
        'S_DIST_07': __state__['S_DIST_07'], 'S_DIST_08': __state__['S_DIST_08'], 'S_DIST_09': __state__['S_DIST_09'],
        'S_DIST_10': __state__['S_DIST_10'], 'S_YTD': __state__['S_YTD'], 'S_ORDER_CNT': __state__['S_ORDER_CNT'],
        'S_REMOTE_CNT': __state__['S_REMOTE_CNT'], 'S_DATA': __state__['S_DATA'],
    }
    ctx.put(__state__)
    return send_reply(ctx, reply_to, data)


@stock_operator.register
async def update_stock(ctx: StatefulFunction, index: int, o_id: int, i_id: int,
                 w_id: int, d_id: int, i_w_id: int, o_entry_d: str, i_qty: int,
                 i_name: str, i_price: float, i_brand_generic: bool, reply_to: list = None) -> Dict:
    __state__ = ctx.get() or {}

    if not bool(__state__):
        raise StockDoesNotExist(f"Stock with key: {ctx.key} does not exist")
    __state__['S_YTD'] += i_qty
    if __state__['S_QUANTITY'] >= i_qty + 10:
        __state__['S_QUANTITY'] -= i_qty
    else:
        __state__['S_QUANTITY'] = __state__['S_QUANTITY'] + 91 - i_qty
    __state__['S_ORDER_CNT'] += 1

    if i_w_id != w_id:
        __state__['S_REMOTE_CNT'] += 1

    if i_brand_generic:
        if "original" in __state__['S_DATA']:
            brand_generic = "B"
        else:
            brand_generic = "G"
    else:
        brand_generic = "G"
    ol_amount = i_qty * i_price
    dist = (
        __state__['S_DIST_01'],
        __state__['S_DIST_02'],
        __state__['S_DIST_03'],
        __state__['S_DIST_04'],
        __state__['S_DIST_05'],
        __state__['S_DIST_06'],
        __state__['S_DIST_07'],
        __state__['S_DIST_08'],
        __state__['S_DIST_09'],
        __state__['S_DIST_10'],
    )
    s_dist_xx = dist[d_id - 1]
    ol_number = index + 1
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'orderline', function_name = 'insert', key = str(w_id) + ":" + str(d_id) + ":" + str(o_id) + ":" + str(ol_number), params = (w_id, d_id, o_id, i_id, ol_number, i_qty, o_entry_d, i_w_id, s_dist_xx, ol_amount, [{'sink': True}]))
    ctx.put(__state__)
    return send_reply(ctx, reply_to, {
        'i_name': i_name,
        'i_price': i_price,
        'ol_amount': ol_amount,
        's_quantity': __state__['S_QUANTITY'],
        'brand_generic': brand_generic,
    })

history_operator = Operator('history', n_partitions=4, composite_key_hash_params=(0, ':'))

@history_operator.register
async def insert(ctx: StatefulFunction, H_C_ID: int, H_C_D_ID: int, H_C_W_ID: int,
             H_D_ID: int, H_W_ID: int, H_DATE: str, H_AMOUNT: float, H_DATA: str, reply_to: list = None):
    __state__ = {}
    __state__['H_C_ID'] = H_C_ID
    __state__['H_C_D_ID'] = H_C_D_ID
    __state__['H_C_W_ID'] = H_C_W_ID
    __state__['H_D_ID'] = H_D_ID
    __state__['H_W_ID'] = H_W_ID
    __state__['H_DATE'] = H_DATE
    __state__['H_AMOUNT'] = H_AMOUNT
    __state__['H_DATA'] = H_DATA
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@history_operator.register
async def get_history(ctx: StatefulFunction, reply_to: list = None) -> dict:
    __state__ = ctx.get() or {}
    if not bool(__state__):
        raise HistoryDoesNotExist(f"History with key: {ctx.key} does not exist")
    data = {
        'H_C_ID': __state__['H_C_ID'], 'H_C_D_ID': __state__['H_C_D_ID'], 'H_C_W_ID': __state__['H_C_W_ID'],
        'H_D_ID': __state__['H_D_ID'], 'H_W_ID': __state__['H_W_ID'], 'H_DATE': __state__['H_DATE'],
        'H_AMOUNT': __state__['H_AMOUNT'], 'H_DATA': __state__['H_DATA'],
    }
    ctx.put(__state__)
    return send_reply(ctx, reply_to, data)

order_operator = Operator('order', n_partitions=4, composite_key_hash_params=(0, ':'))

@order_operator.register
async def insert(ctx: StatefulFunction, O_W_ID: int, O_D_ID: int, O_ID: int, O_C_ID: int = 0, O_ENTRY_D: str = "", O_CARRIER_ID: Optional[int] = None, O_OL_CNT: int = 0, O_ALL_LOCAL: bool = True, reply_to: list = None):
    __state__ = {}
    __state__['O_W_ID'] = O_W_ID
    __state__['O_D_ID'] = O_D_ID
    __state__['O_ID'] = O_ID
    __state__['O_C_ID'] = O_C_ID
    __state__['O_ENTRY_D'] = O_ENTRY_D
    __state__['O_CARRIER_ID'] = O_CARRIER_ID
    __state__['O_OL_CNT'] = O_OL_CNT
    __state__['O_ALL_LOCAL'] = O_ALL_LOCAL
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@order_operator.register
async def get_order(ctx: StatefulFunction, c_id: int, entry_d: str, ol_cnt: int, all_local: bool, reply_to: list = None) -> dict:
    __state__ = ctx.get() or {}
    data = {
        'O_W_ID': __state__['O_W_ID'], 'O_D_ID': __state__['O_D_ID'], 'O_ID': __state__['O_ID'], 'O_C_ID': c_id, 'O_ENTRY_D': entry_d,
        'O_OL_CNT': ol_cnt, 'O_ALL_LOCAL': all_local,
    }
    if not bool(__state__):
        raise OrderDoesNotExist(f"Order with key: {ctx.key} does not exist")
    ctx.put(__state__)
    return send_reply(ctx, reply_to, data)

neworder_operator = Operator('neworder', n_partitions=4, composite_key_hash_params=(0, ':'))

@neworder_operator.register
async def insert(ctx: StatefulFunction, NO_W_ID: int, NO_D_ID: int, NO_O_ID: int, reply_to: list = None):
    __state__ = {}
    __state__['NO_W_ID'] = NO_W_ID
    __state__['NO_D_ID'] = NO_D_ID
    __state__['NO_O_ID'] = NO_O_ID
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@neworder_operator.register
async def create(ctx: StatefulFunction, no_o_id: int, no_d_id: int, no_w_id: int, reply_to: list = None) -> None:
    __state__ = ctx.get() or {}
    __state__['NO_O_ID'] = no_o_id
    __state__['NO_D_ID'] = no_d_id
    __state__['NO_W_ID'] = no_w_id
    ctx.put(__state__)

orderline_operator = Operator('orderline', n_partitions=4, composite_key_hash_params=(0, ':'))

@orderline_operator.register
async def insert(
    ctx: StatefulFunction, OL_W_ID: int,
    OL_D_ID: int,
    OL_O_ID: int,
    OL_I_ID: int,
    OL_NUMBER: int,
    OL_QUANTITY: int = 0,
    OL_DELIVERY_D: Optional[str] = None,
    OL_SUPPLY_W_ID: Optional[int] = None,
    OL_DIST_INFO: str = "",
    OL_AMOUNT: float = 0.0, 
reply_to: list = None):
    __state__ = {}
    __state__['OL_W_ID'] = OL_W_ID
    __state__['OL_D_ID'] = OL_D_ID
    __state__['OL_O_ID'] = OL_O_ID
    __state__['OL_I_ID'] = OL_I_ID
    __state__['OL_NUMBER'] = OL_NUMBER
    __state__['OL_QUANTITY'] = OL_QUANTITY
    __state__['OL_DELIVERY_D'] = OL_DELIVERY_D
    __state__['OL_SUPPLY_W_ID'] = OL_SUPPLY_W_ID
    __state__['OL_DIST_INFO'] = OL_DIST_INFO
    __state__['OL_AMOUNT'] = OL_AMOUNT
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@orderline_operator.register
async def get_order_line(ctx: StatefulFunction, reply_to: list = None) -> dict:
    __state__ = ctx.get() or {}
    if not bool(__state__):
        raise OrderLineDoesNotExist(f"OrderLine with key: {ctx.key} does not exist")
    data = {
        'OL_W_ID': __state__['OL_W_ID'], 'OL_D_ID': __state__['OL_D_ID'], 'OL_O_ID': __state__['OL_O_ID'],
        'OL_I_ID': __state__['OL_I_ID'].I_ID, 'OL_NUMBER': __state__['OL_NUMBER'], 'OL_QUANTITY': __state__['OL_QUANTITY'],
        'OL_DELIVERY_D': __state__['OL_DELIVERY_D'], 'OL_SUPPLY_W_ID': __state__['OL_SUPPLY_W_ID'] if __state__['OL_SUPPLY_W_ID'] else None,
        'OL_DIST_INFO': __state__['OL_DIST_INFO'], 'OL_AMOUNT': __state__['OL_AMOUNT'],
    }
    ctx.put(__state__)
    return send_reply(ctx, reply_to, data)

newordertxn_operator = Operator('newordertxn', n_partitions=4)

@newordertxn_operator.register
async def insert(ctx: StatefulFunction, txn_id: str, reply_to: list = None):
    __state__ = {}
    __state__['txn_id'] = txn_id
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@newordertxn_operator.register
async def new_order(ctx: StatefulFunction, params: dict, reply_to: list = None) -> str:
    w_id: int = params["W_ID"]
    d_id: int = params["D_ID"]
    c_id: int = params["C_ID"]
    o_entry_d: str = params["O_ENTRY_D"]
    i_ids: list[int] = params["I_IDS"]
    i_w_ids: list[int] = params["I_W_IDS"]
    i_qtys: list[int] = params["I_QTYS"]
    assert len(i_ids) > 0
    assert len(i_ids) == len(i_w_ids) == len(i_qtys)
    all_local = True
    for item_w_id in i_w_ids:
        if item_w_id != w_id:
            all_local = False
            break
    district = f"{w_id}:{d_id}"
    customer = f"{w_id}:{d_id}:{c_id}"
    reply_to = push_continuation(ctx, reply_to, 'newordertxn', 'new_order_step_2', ctx.key, {'all_local': all_local, 'c_id': c_id, 'customer': customer, 'd_id': d_id, 'district': district, 'i_ids': i_ids, 'i_qtys': i_qtys, 'i_w_ids': i_w_ids, 'o_entry_d': o_entry_d, 'w_id': w_id})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'get_warehouse', key = w_id, params = (reply_to,))

@newordertxn_operator.register
async def new_order_step_2(ctx: StatefulFunction, func_context, warehouse_data = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (all_local, c_id, customer, d_id, district, i_ids, i_qtys, i_w_ids, o_entry_d, w_id) = (params.get('all_local'), params.get('c_id'), params.get('customer'), params.get('d_id'), params.get('district'), params.get('i_ids'), params.get('i_qtys'), params.get('i_w_ids'), params.get('o_entry_d'), params.get('w_id'))
    reply_to = push_continuation(ctx, reply_to, 'newordertxn', 'new_order_step_3', ctx.key, {'customer': customer, 'o_entry_d': o_entry_d, 'warehouse_data': warehouse_data})
    ctx.call_remote_async(operator_name = 'district', function_name = 'get_district', key = district, params = (w_id, d_id, c_id, o_entry_d, i_ids, i_qtys, i_w_ids, all_local, reply_to))

@newordertxn_operator.register
async def new_order_step_3(ctx: StatefulFunction, func_context, district_bundle = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, o_entry_d, warehouse_data) = (params.get('customer'), params.get('o_entry_d'), params.get('warehouse_data'))
    reply_to = push_continuation(ctx, reply_to, 'newordertxn', 'new_order_step_4', ctx.key, {'district_bundle': district_bundle, 'o_entry_d': o_entry_d, 'warehouse_data': warehouse_data})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'get_customer', key = customer, params = (reply_to,))

@newordertxn_operator.register
async def new_order_step_4(ctx: StatefulFunction, func_context, customer_data = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (district_bundle, o_entry_d, warehouse_data) = (params.get('district_bundle'), params.get('o_entry_d'), params.get('warehouse_data'))
    district_data = district_bundle['district']
    item_replies = district_bundle['items']
    total = sum(item_reply['ol_amount'] for item_reply in item_replies)
    w_tax: float = warehouse_data['W_TAX']
    d_tax: float = district_data['D_TAX']
    total = total * (1 - customer_data['C_DISCOUNT']) * (1 + w_tax + d_tax)
    o_id = district_data['D_NEXT_O_ID']
    attr_4 = ";"
    item_str = attr_4.join(
        f"{r['i_name']},{r['s_quantity']},{r['brand_generic']},{r['i_price']:.2f},{r['ol_amount']:.2f}"
        for r in item_replies
    )
    return send_reply(ctx, reply_to, (
        f"NO|C_ID={customer_data['C_ID']},C_LAST={customer_data['C_LAST']},"
        f"C_CREDIT={customer_data['C_CREDIT']},"
        f"C_DISCOUNT={customer_data['C_DISCOUNT']:.4f},W_TAX={w_tax:.4f},D_TAX={d_tax:.4f},"
        f"O_ID={o_id},O_ENTRY_D={o_entry_d},N_ITEMS={len(item_replies)},"
        f"TOTAL={total:.2f},ITEMS=[{item_str}]"
    ))

paymenttxn_operator = Operator('paymenttxn', n_partitions=4)

@paymenttxn_operator.register
async def insert(
    ctx: StatefulFunction, txn_id: str,
    w_id: int,
    c_w_id: int,
    d_id: int = 0,
    c_d_id: int = 0,
    h_amount: float = 0.0,
    h_date: str = "",
reply_to: list = None):
    __state__ = {}
    __state__['txn_id'] = txn_id
    __state__['W_ID'] = w_id
    __state__['D_ID'] = d_id
    __state__['C_W_ID'] = c_w_id
    __state__['C_D_ID'] = c_d_id
    __state__['C_ID'] = None
    __state__['H_AMOUNT'] = h_amount
    __state__['H_DATE'] = h_date
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)



@paymenttxn_operator.register
async def get_customer_data(ctx: StatefulFunction, c_last: Optional[str], reply_to: list = None) -> Dict:
    __state__ = ctx.get() or {}
    if __state__['C_ID'] is not None:
        customer = f"{__state__['C_W_ID']}:{__state__['C_D_ID']}:{__state__['C_ID']}"
        ctx.put(__state__)
        ctx.call_remote_async(operator_name = 'customer', function_name = 'pay', key = customer, params = (__state__['H_AMOUNT'], __state__['D_ID'], __state__['W_ID'], reply_to))
    else:
        customer_idx = f"{__state__['C_W_ID']}:{__state__['C_D_ID']}:{c_last}"
        ctx.put(__state__)
        ctx.call_remote_async(operator_name = 'customerindex', function_name = 'pay', key = customer_idx, params = (__state__['H_AMOUNT'], __state__['D_ID'], __state__['W_ID'], reply_to))


@paymenttxn_operator.register
async def payment(ctx: StatefulFunction, params: dict, reply_to: list = None) -> str:
    __state__ = ctx.get() or {}
    w_id: int = params["W_ID"]
    d_id: int = int(params["D_ID"])
    h_amount: float = params["H_AMOUNT"]
    c_w_id: int = params["C_W_ID"]
    c_d_id: int = int(params["C_D_ID"])
    attr_1 = params.get("C_ID")
    c_id: Optional[int] = int(params["C_ID"]) if attr_1 is not None else None
    attr_2 = params.get("C_LAST")
    c_last: Optional[str] = attr_2
    h_date: str = params["H_DATE"]
    __state__['W_ID'] = w_id
    __state__['D_ID'] = d_id
    __state__['C_ID'] = c_id
    __state__['C_W_ID'] = c_w_id
    __state__['C_D_ID'] = c_d_id
    __state__['H_DATE'] = h_date
    __state__['H_AMOUNT'] = h_amount
    district = f"{w_id}:{d_id}"
    reply_to = push_continuation(ctx, reply_to, 'paymenttxn', 'payment_step_2', ctx.key, {'district': district, 'h_amount': h_amount, 'w_id': w_id})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'paymenttxn', function_name = 'get_customer_data', key = ctx.key, params = (c_last, reply_to))

@paymenttxn_operator.register
async def payment_step_2(ctx: StatefulFunction, func_context, customer_data = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (district, h_amount, w_id) = (params.get('district'), params.get('h_amount'), params.get('w_id'))
    reply_to = push_continuation(ctx, reply_to, 'paymenttxn', 'payment_step_3', ctx.key, {'customer_data': customer_data, 'h_amount': h_amount, 'w_id': w_id})
    ctx.call_remote_async(operator_name = 'district', function_name = 'pay', key = district, params = (h_amount, reply_to))

@paymenttxn_operator.register
async def payment_step_3(ctx: StatefulFunction, func_context, district_data = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer_data, h_amount, w_id) = (params.get('customer_data'), params.get('h_amount'), params.get('w_id'))
    reply_to = push_continuation(ctx, reply_to, 'paymenttxn', 'payment_step_4', ctx.key, {'customer_data': customer_data, 'district_data': district_data})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'pay', key = w_id, params = (h_amount, reply_to))

@paymenttxn_operator.register
async def payment_step_4(ctx: StatefulFunction, func_context, warehouse_data = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (customer_data, district_data) = (params.get('customer_data'), params.get('district_data'))
    h_data = f"{warehouse_data['W_NAME']}    {district_data['D_NAME']}"
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'history', function_name = 'insert', key = str(__state__['W_ID']) + ":" + str(__state__['D_ID']) + ":" + str(customer_data['C_ID']), params = (customer_data['C_ID'], __state__['C_D_ID'], __state__['C_W_ID'], __state__['D_ID'], __state__['W_ID'], __state__['H_DATE'], __state__['H_AMOUNT'], h_data, [{'sink': True}]))

    if customer_data['C_CREDIT'] == "BC":
        c_data_str = f",C_DATA={customer_data['C_DATA'][:200]}"
    else:
        c_data_str = ""
    ctx.put(__state__)
    return send_reply(ctx, reply_to, (
        f"P|W_ID={__state__['W_ID']},D_ID={district_data['D_ID']},C_ID={customer_data['C_ID']},"
        f"C_D_ID={customer_data['C_D_ID']},C_W_ID={customer_data['C_W_ID']},"
        f"C_NAME={customer_data['C_FIRST']} {customer_data['C_MIDDLE']} {customer_data['C_LAST']},"
        f"C_BAL={customer_data['C_BALANCE']:.2f},C_DISCOUNT={customer_data['C_DISCOUNT']:.4f},"
        f"C_CREDIT={customer_data['C_CREDIT']},W_TAX={warehouse_data['W_TAX']:.4f},"
        f"D_TAX={district_data['D_TAX']:.4f},H_AMOUNT={__state__['H_AMOUNT']:.2f},"
        f"H_DATE={__state__['H_DATE']}{c_data_str}"
    ))

