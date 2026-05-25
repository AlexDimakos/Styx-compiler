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

from typing import Optional
from styx_compiler.core import entity, send_async


# ──────────────────────────────────────────
# Custom Exceptions (all trigger rollback)
# ──────────────────────────────────────────

class InsufficientFunds(Exception):
    pass

class InsufficientStock(Exception):
    pass

class InvalidCoupon(Exception):
    pass

class SellerSuspended(Exception):
    pass

class OrderAlreadyFulfilled(Exception):
    pass

class WarehouseCapacityExceeded(Exception):
    pass

class ReviewAlreadySubmitted(Exception):
    pass
product_operator = Operator('product', n_partitions=4)

@product_operator.register
async def insert(ctx: StatefulFunction, product_id: str, name: str, base_price: int, seller: 'Seller', reply_to: list = None):
    __state__ = {}
    __state__['product_id'] = product_id
    __state__['name'] = name
    __state__['base_price'] = base_price
    __state__['seller'] = seller
    __state__['stock'] = 0
    __state__['total_sold'] = 0
    __state__['rating_sum'] = 0
    __state__['rating_count'] = 0
    __state__['tags'] = []
    __state__['is_active'] = True
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@product_operator.register
async def get_product_id(ctx: StatefulFunction, reply_to: list = None) -> str:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['product_id'])


@product_operator.register
async def get_price(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['base_price'])


@product_operator.register
async def get_stock(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['stock'])


@product_operator.register
async def get_seller(ctx: StatefulFunction, reply_to: list = None) -> 'Seller':
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['seller'])


@product_operator.register
async def is_available(ctx: StatefulFunction, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['is_active'] & (__state__['stock'] > 0))


@product_operator.register
async def get_average_rating(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    if __state__['rating_count'] == 0:
        ctx.put(__state__)
        return send_reply(ctx, reply_to, 0)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['rating_sum'] // __state__['rating_count'])


@product_operator.register
async def add_stock(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if amount <= 0:
        raise InsufficientStock("Stock amount must be positive.")
    __state__['stock'] += amount
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@product_operator.register
async def deduct_stock(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if amount <= 0:
        raise InsufficientStock("Amount must be positive.")
    if not __state__['is_active']:
        raise InsufficientStock("Product is no longer active.")
    if __state__['stock'] < amount:
        raise InsufficientStock("Not enough stock for product.")
    __state__['stock'] -= amount
    __state__['total_sold'] += amount
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)



@product_operator.register
async def add_rating(ctx: StatefulFunction, score: int, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    if (score < 0) | (score > 10):
        raise ValueError("Rating must be between 0 and 10.")
    __state__['rating_sum'] += score
    __state__['rating_count'] += 1
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_average_rating', key = ctx.key, params = (reply_to,))


@product_operator.register
async def deactivate(ctx: StatefulFunction, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    __state__['is_active'] = False
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@product_operator.register
async def add_tag(ctx: StatefulFunction, tag: str, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if tag not in __state__['tags']:
        __state__['tags'].append(tag)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@product_operator.register
async def get_tags(ctx: StatefulFunction, reply_to: list = None) -> list[str]:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['tags'])


@product_operator.register
async def get_total_sold(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['total_sold'])


@product_operator.register
async def get_popularity_score(ctx: StatefulFunction, reply_to: list = None) -> int:
    reply_to = push_continuation(ctx, reply_to, 'product', 'get_popularity_score_step_2', ctx.key, {})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_average_rating', key = ctx.key, params = (reply_to,))

@product_operator.register
async def get_popularity_score_step_2(ctx: StatefulFunction, func_context, avg = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['total_sold'] * 10 + avg * 50 + __state__['rating_count'] * 5)

seller_operator = Operator('seller', n_partitions=4)

@seller_operator.register
async def insert(ctx: StatefulFunction, seller_id: str, name: str, reply_to: list = None):
    __state__ = {}
    __state__['seller_id'] = seller_id
    __state__['name'] = name
    __state__['balance'] = 0
    __state__['products'] = []
    __state__['total_revenue'] = 0
    __state__['is_suspended'] = False
    __state__['penalty_points'] = 0
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@seller_operator.register
async def get_seller_id(ctx: StatefulFunction, reply_to: list = None) -> str:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['seller_id'])


@seller_operator.register
async def is_active(ctx: StatefulFunction, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, not __state__['is_suspended'])


@seller_operator.register
async def get_balance(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['balance'])


@seller_operator.register
async def get_revenue(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['total_revenue'])


@seller_operator.register
async def add_product(ctx: StatefulFunction, product: 'Product', reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if __state__['is_suspended']:
        raise SellerSuspended("Seller is suspended and cannot add products.")
    __state__['products'].append(product)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@seller_operator.register
async def credit_sale(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if __state__['is_suspended']:
        raise SellerSuspended("Seller is suspended.")
    __state__['balance'] += amount
    __state__['total_revenue'] += amount
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@seller_operator.register
async def debit_penalty(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    __state__['penalty_points'] += 1
    __state__['balance'] -= amount
    if __state__['penalty_points'] >= 5:
        __state__['is_suspended'] = True
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@seller_operator.register
async def withdraw(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if __state__['balance'] < amount:
        raise InsufficientFunds("Seller does not have enough balance to withdraw.")
    __state__['balance'] -= amount
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@seller_operator.register
async def get_products(ctx: StatefulFunction, reply_to: list = None) -> list['Product']:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['products'])


@seller_operator.register
async def get_penalty_points(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['penalty_points'])


@seller_operator.register
async def reinstate(ctx: StatefulFunction, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    __state__['is_suspended'] = False
    __state__['penalty_points'] = 0
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)

customer_operator = Operator('customer', n_partitions=4)

@customer_operator.register
async def insert(ctx: StatefulFunction, customer_id: str, username: str, reply_to: list = None):
    __state__ = {}
    __state__['customer_id'] = customer_id
    __state__['username'] = username
    __state__['balance'] = 0
    __state__['cart'] = []
    __state__['order_history'] = []
    __state__['wishlist'] = []
    __state__['loyalty_points'] = 0
    __state__['reviewed_products'] = []
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@customer_operator.register
async def get_order_history(ctx: StatefulFunction, reply_to: list = None) -> list[str]:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['order_history'])


@customer_operator.register
async def get_balance(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['balance'])


@customer_operator.register
async def get_loyalty_points(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['loyalty_points'])


@customer_operator.register
async def add_funds(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    __state__['balance'] += amount
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@customer_operator.register
async def deduct_funds(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if __state__['balance'] < amount:
        raise InsufficientFunds("Customer does not have enough balance.")
    __state__['balance'] -= amount
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@customer_operator.register
async def add_to_cart(ctx: StatefulFunction, product_id: str, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if product_id not in __state__['cart']:
        __state__['cart'].append(product_id)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@customer_operator.register
async def remove_from_cart(ctx: StatefulFunction, product_id: str, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if product_id in __state__['cart']:
        __state__['cart'].remove(product_id)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@customer_operator.register
async def clear_cart(ctx: StatefulFunction, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    __state__['cart'] = []
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@customer_operator.register
async def get_cart(ctx: StatefulFunction, reply_to: list = None) -> list[str]:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['cart'])


@customer_operator.register
async def add_to_wishlist(ctx: StatefulFunction, product_id: str, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if product_id not in __state__['wishlist']:
        __state__['wishlist'].append(product_id)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@customer_operator.register
async def add_order(ctx: StatefulFunction, order_id: str, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    __state__['order_history'].append(order_id)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@customer_operator.register
async def earn_loyalty_points(ctx: StatefulFunction, amount_spent: int, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    earned = amount_spent // 100
    __state__['loyalty_points'] += earned
    ctx.put(__state__)
    return send_reply(ctx, reply_to, earned)


@customer_operator.register
async def redeem_loyalty_points(ctx: StatefulFunction, points: int, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    if __state__['loyalty_points'] < points:
        points = __state__['loyalty_points']
    __state__['loyalty_points'] -= points
    ctx.put(__state__)
    return send_reply(ctx, reply_to, points * 10)


@customer_operator.register
async def has_reviewed(ctx: StatefulFunction, product_id: str, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, product_id in __state__['reviewed_products'])


@customer_operator.register
async def mark_reviewed(ctx: StatefulFunction, product_id: str, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if product_id in __state__['reviewed_products']:
        raise ReviewAlreadySubmitted("Customer already reviewed this product.")
    __state__['reviewed_products'].append(product_id)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@customer_operator.register
async def get_order_count(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, len(__state__['order_history']))


@customer_operator.register
async def get_wishlist(ctx: StatefulFunction, reply_to: list = None) -> list[str]:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['wishlist'])

coupon_operator = Operator('coupon', n_partitions=4)

@coupon_operator.register
async def insert(ctx: StatefulFunction, code: str, discount_percent: int, max_uses: int, min_order_value: int, reply_to: list = None):
    __state__ = {}
    __state__['code'] = code
    __state__['discount_percent'] = discount_percent
    __state__['max_uses'] = max_uses
    __state__['uses'] = 0
    __state__['min_order_value'] = min_order_value
    __state__['is_active'] = True
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@coupon_operator.register
async def is_valid(ctx: StatefulFunction, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['is_active'] & (__state__['uses'] < __state__['max_uses']))


@coupon_operator.register
async def get_discount_percent(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['discount_percent'])


@coupon_operator.register
async def get_min_order_value(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['min_order_value'])


@coupon_operator.register
async def apply(ctx: StatefulFunction, order_value: int, reply_to: list = None) -> int:
    reply_to = push_continuation(ctx, reply_to, 'coupon', 'apply_step_2', ctx.key, {'order_value': order_value})
    ctx.call_remote_async(operator_name = 'coupon', function_name = 'is_valid', key = ctx.key, params = (reply_to,))

@coupon_operator.register
async def apply_step_2(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (order_value,) = (params.get('order_value'),)
    if not attr_1:
        raise InvalidCoupon("Coupon is expired or has reached max uses.")
    if order_value < __state__['min_order_value']:
        raise InvalidCoupon("Order value too low for this coupon.")
    __state__['uses'] += 1
    discount = (order_value * __state__['discount_percent']) // 100
    ctx.put(__state__)
    return send_reply(ctx, reply_to, discount)


@coupon_operator.register
async def deactivate(ctx: StatefulFunction, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    __state__['is_active'] = False
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@coupon_operator.register
async def get_remaining_uses(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['max_uses'] - __state__['uses'])

warehouse_operator = Operator('warehouse', n_partitions=4)

@warehouse_operator.register
async def insert(ctx: StatefulFunction, warehouse_id: str, capacity: int, reply_to: list = None):
    __state__ = {}
    __state__['warehouse_id'] = warehouse_id
    __state__['capacity'] = capacity
    __state__['used_capacity'] = 0
    __state__['product_slots'] = {}
    __state__['pending_shipments'] = []
    __state__['total_shipped'] = 0
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@warehouse_operator.register
async def get_available_capacity(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['capacity'] - __state__['used_capacity'])


@warehouse_operator.register
async def get_used_capacity(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['used_capacity'])


@warehouse_operator.register
async def store_product(ctx: StatefulFunction, product_id: str, quantity: int, reply_to: list = None) -> bool:
    reply_to = push_continuation(ctx, reply_to, 'warehouse', 'store_product_step_2', ctx.key, {'product_id': product_id, 'quantity': quantity})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'get_available_capacity', key = ctx.key, params = (reply_to,))

@warehouse_operator.register
async def store_product_step_2(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (product_id, quantity) = (params.get('product_id'), params.get('quantity'))
    if quantity > attr_1:
        raise WarehouseCapacityExceeded("Not enough space in warehouse.")
    current = __state__['product_slots'].get(product_id, 0)
    __state__['product_slots'][product_id] = current + quantity
    __state__['used_capacity'] += quantity
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@warehouse_operator.register
async def remove_product(ctx: StatefulFunction, product_id: str, quantity: int, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if quantity <= 0:
        raise InsufficientStock("Quantity must be positive.")
    current = __state__['product_slots'].get(product_id, 0)
    if current < quantity:
        raise InsufficientStock("Not enough of this product in warehouse.")
    new_qty = current - quantity

    if new_qty == 0:
        del __state__['product_slots'][product_id]
    else:
        __state__['product_slots'][product_id] = new_qty
    __state__['used_capacity'] -= quantity
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@warehouse_operator.register
async def get_product_quantity(ctx: StatefulFunction, product_id: str, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    attr_1 = __state__['product_slots'].get(product_id, 0)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, attr_1)


@warehouse_operator.register
async def add_pending_shipment(ctx: StatefulFunction, order_id: str, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    __state__['pending_shipments'].append(order_id)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@warehouse_operator.register
async def dispatch_shipment(ctx: StatefulFunction, order_id: str, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if order_id not in __state__['pending_shipments']:
        raise OrderAlreadyFulfilled("Order not found in pending shipments.")
    __state__['pending_shipments'].remove(order_id)
    __state__['total_shipped'] += 1
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@warehouse_operator.register
async def get_total_shipped(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['total_shipped'])


@warehouse_operator.register
async def get_pending_count(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, len(__state__['pending_shipments']))


@warehouse_operator.register
async def calculate_fill_rate(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    if __state__['capacity'] == 0:
        ctx.put(__state__)
        return send_reply(ctx, reply_to, 0)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, (__state__['used_capacity'] * 100) // __state__['capacity'])

marketplace_operator = Operator('marketplace', n_partitions=4)

@marketplace_operator.register
async def insert(ctx: StatefulFunction, marketplace_id: str, reply_to: list = None):
    __state__ = {}
    __state__['marketplace_id'] = marketplace_id
    __state__['registered_sellers'] = []
    __state__['registered_customers'] = []
    __state__['all_products'] = []
    __state__['total_transactions'] = 0
    __state__['total_revenue'] = 0
    __state__['platform_fee_percent'] = 5
    ctx.put_func_context({})
    ctx.put(__state__)
    return send_reply(ctx, reply_to, ctx.key)


@marketplace_operator.register
async def register_seller(ctx: StatefulFunction, seller_id: str, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if seller_id not in __state__['registered_sellers']:
        __state__['registered_sellers'].append(seller_id)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@marketplace_operator.register
async def register_customer(ctx: StatefulFunction, customer_id: str, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if customer_id not in __state__['registered_customers']:
        __state__['registered_customers'].append(customer_id)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@marketplace_operator.register
async def list_product(ctx: StatefulFunction, product_id: str, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    if product_id not in __state__['all_products']:
        __state__['all_products'].append(product_id)
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@marketplace_operator.register
async def record_transaction(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    __state__ = ctx.get() or {}
    fee = (amount * __state__['platform_fee_percent']) // 100
    __state__['total_revenue'] += fee
    __state__['total_transactions'] += 1
    ctx.put(__state__)
    return send_reply(ctx, reply_to, True)


@marketplace_operator.register
async def get_stats(ctx: StatefulFunction, reply_to: list = None) -> dict[str, int]:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, {
        "sellers": len(__state__['registered_sellers']),
        "customers": len(__state__['registered_customers']),
        "products": len(__state__['all_products']),
        "transactions": __state__['total_transactions'],
        "revenue": __state__['total_revenue'],
    })


@marketplace_operator.register
async def get_platform_fee(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['platform_fee_percent'])


@marketplace_operator.register
async def get_total_revenue(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, __state__['total_revenue'])


@marketplace_operator.register
async def get_product_count(ctx: StatefulFunction, reply_to: list = None) -> int:
    __state__ = ctx.get() or {}
    ctx.put(__state__)
    return send_reply(ctx, reply_to, len(__state__['all_products']))


# ── Complex orchestration methods ──────

@marketplace_operator.register
async def purchase(
    ctx: StatefulFunction, customer: str,
    product: str,
    warehouse: str,
    quantity: int,
    coupon_code: Optional[str],
    coupon: Optional[str],
    use_loyalty: bool,
reply_to: list = None) -> str:

    if quantity <= 0:
        raise ValueError("Quantity must be positive.")
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_2', ctx.key, {'coupon': coupon, 'coupon_code': coupon_code, 'customer': customer, 'product': product, 'quantity': quantity, 'use_loyalty': use_loyalty, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_seller', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_2(ctx: StatefulFunction, func_context, seller = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (coupon, coupon_code, customer, product, quantity, use_loyalty, warehouse) = (params.get('coupon'), params.get('coupon_code'), params.get('customer'), params.get('product'), params.get('quantity'), params.get('use_loyalty'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_3', ctx.key, {'coupon': coupon, 'coupon_code': coupon_code, 'customer': customer, 'product': product, 'quantity': quantity, 'seller': seller, 'use_loyalty': use_loyalty, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'is_available', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_3(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (coupon, coupon_code, customer, product, quantity, seller, use_loyalty, warehouse) = (params.get('coupon'), params.get('coupon_code'), params.get('customer'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('use_loyalty'), params.get('warehouse'))

    if not attr_2:
        raise InsufficientStock("Product not available.")
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_4', ctx.key, {'coupon': coupon, 'coupon_code': coupon_code, 'customer': customer, 'product': product, 'quantity': quantity, 'seller': seller, 'use_loyalty': use_loyalty, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'is_active', key = seller, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_4(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (coupon, coupon_code, customer, product, quantity, seller, use_loyalty, warehouse) = (params.get('coupon'), params.get('coupon_code'), params.get('customer'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('use_loyalty'), params.get('warehouse'))

    if not attr_3:
        raise SellerSuspended("Seller is suspended.")
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_5', ctx.key, {'coupon': coupon, 'coupon_code': coupon_code, 'customer': customer, 'product': product, 'quantity': quantity, 'seller': seller, 'use_loyalty': use_loyalty, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_5(ctx: StatefulFunction, func_context, attr_4 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (coupon, coupon_code, customer, product, quantity, seller, use_loyalty, warehouse) = (params.get('coupon'), params.get('coupon_code'), params.get('customer'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('use_loyalty'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_6', ctx.key, {'coupon': coupon, 'coupon_code': coupon_code, 'customer': customer, 'product': product, 'quantity': quantity, 'seller': seller, 'use_loyalty': use_loyalty, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'get_product_quantity', key = warehouse, params = (attr_4, reply_to))

@marketplace_operator.register
async def purchase_step_6(ctx: StatefulFunction, func_context, attr_5 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (coupon, coupon_code, customer, product, quantity, seller, use_loyalty, warehouse) = (params.get('coupon'), params.get('coupon_code'), params.get('customer'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('use_loyalty'), params.get('warehouse'))

    if attr_5 < quantity:
        raise InsufficientStock("Warehouse does not have enough stock.")
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_7', ctx.key, {'coupon': coupon, 'coupon_code': coupon_code, 'customer': customer, 'product': product, 'quantity': quantity, 'seller': seller, 'use_loyalty': use_loyalty, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_price', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_7(ctx: StatefulFunction, func_context, attr_6 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (coupon, coupon_code, customer, product, quantity, seller, use_loyalty, warehouse) = (params.get('coupon'), params.get('coupon_code'), params.get('customer'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('use_loyalty'), params.get('warehouse'))
    base_cost = attr_6 * quantity
    discount = 0

    if coupon is not None:
        if coupon_code is not None:
            if coupon.code != coupon_code:
                raise InvalidCoupon("Coupon code mismatch.")
            reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_8', ctx.key, {'base_cost': base_cost, 'customer': customer, 'discount': discount, 'product': product, 'quantity': quantity, 'seller': seller, 'use_loyalty': use_loyalty, 'warehouse': warehouse})
            ctx.call_remote_async(operator_name = 'coupon', function_name = 'apply', key = coupon, params = (base_cost, reply_to))
        else:
            loyalty_discount = 0
            if use_loyalty:
                max_loyalty_discount = int(base_cost * 0.3)
                reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_43', ctx.key, {'base_cost': base_cost, 'customer': customer, 'discount': discount, 'max_loyalty_discount': max_loyalty_discount, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
                ctx.call_remote_async(operator_name = 'customer', function_name = 'get_loyalty_points', key = customer, params = (reply_to,))
            else:
                final_cost = base_cost - discount - loyalty_discount
                if final_cost < 0:
                    final_cost = 0
                reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_61', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
                ctx.call_remote_async(operator_name = 'customer', function_name = 'deduct_funds', key = customer, params = (final_cost, reply_to))
    else:
        loyalty_discount = 0
        if use_loyalty:
            max_loyalty_discount = int(base_cost * 0.3)
            reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_77', ctx.key, {'base_cost': base_cost, 'customer': customer, 'discount': discount, 'max_loyalty_discount': max_loyalty_discount, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
            ctx.call_remote_async(operator_name = 'customer', function_name = 'get_loyalty_points', key = customer, params = (reply_to,))
        else:
            final_cost = base_cost - discount - loyalty_discount
            if final_cost < 0:
                final_cost = 0
            reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_95', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
            ctx.call_remote_async(operator_name = 'customer', function_name = 'deduct_funds', key = customer, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_8(ctx: StatefulFunction, func_context, discount = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (base_cost, customer, discount, product, quantity, seller, use_loyalty, warehouse) = (params.get('base_cost'), params.get('customer'), params.get('discount'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('use_loyalty'), params.get('warehouse'))
    loyalty_discount = 0
    if use_loyalty:
        max_loyalty_discount = int(base_cost * 0.3)
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_9', ctx.key, {'base_cost': base_cost, 'customer': customer, 'discount': discount, 'max_loyalty_discount': max_loyalty_discount, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'customer', function_name = 'get_loyalty_points', key = customer, params = (reply_to,))
    else:
        final_cost = base_cost - discount - loyalty_discount
        if final_cost < 0:
            final_cost = 0
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_27', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'customer', function_name = 'deduct_funds', key = customer, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_9(ctx: StatefulFunction, func_context, attr_8 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (base_cost, customer, discount, max_loyalty_discount, product, quantity, seller, warehouse) = (params.get('base_cost'), params.get('customer'), params.get('discount'), params.get('max_loyalty_discount'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    redeemable_points = attr_8 // 2
    potential_discount = redeemable_points * 10
    actual_discount = min(max_loyalty_discount, potential_discount)
    points_to_use = actual_discount // 10
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_10', ctx.key, {'base_cost': base_cost, 'customer': customer, 'discount': discount, 'loyalty_discount': loyalty_discount, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'redeem_loyalty_points', key = customer, params = (points_to_use, reply_to))

@marketplace_operator.register
async def purchase_step_10(ctx: StatefulFunction, func_context, loyalty_discount = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (base_cost, customer, discount, loyalty_discount, product, quantity, seller, warehouse) = (params.get('base_cost'), params.get('customer'), params.get('discount'), params.get('loyalty_discount'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    final_cost = base_cost - discount - loyalty_discount
    if final_cost < 0:
        final_cost = 0
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_11', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'deduct_funds', key = customer, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_11(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_12', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'deduct_stock', key = product, params = (quantity, reply_to))

@marketplace_operator.register
async def purchase_step_12(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_13', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_13(ctx: StatefulFunction, func_context, attr_12 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_14', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'remove_product', key = warehouse, params = (attr_12, quantity, reply_to))

@marketplace_operator.register
async def purchase_step_14(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))
    platform_fee = (final_cost * __state__['platform_fee_percent']) // 100
    seller_cut = final_cost - platform_fee
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_15', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'seller', function_name = 'credit_sale', key = seller, params = (seller_cut, reply_to))

@marketplace_operator.register
async def purchase_step_15(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))

    if final_cost > 0:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_16', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'customer', function_name = 'earn_loyalty_points', key = customer, params = (final_cost, reply_to))
    else:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_22', ctx.key, {'customer': customer, 'product': product, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_16(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_17', ctx.key, {'customer': customer, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_17(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, seller, warehouse) = (params.get('customer'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_18', ctx.key, {'customer': customer, 'product': product, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_18(ctx: StatefulFunction, func_context, attr_17 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, warehouse) = (params.get('customer'), params.get('product'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_19', ctx.key, {'attr_17': attr_17, 'customer': customer, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_19(ctx: StatefulFunction, func_context, attr_18 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (attr_17, customer, warehouse) = (params.get('attr_17'), params.get('customer'), params.get('warehouse'))
    order_id = (
        attr_17
        + "_"
        + attr_18
        + "_"
        + str(__state__['total_transactions'])
    )
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_20', ctx.key, {'order_id': order_id, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'customer', function_name = 'add_order', key = customer, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_20(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id, warehouse) = (params.get('order_id'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_21', ctx.key, {'order_id': order_id})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'add_pending_shipment', key = warehouse, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_21(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id,) = (params.get('order_id'),)
    return send_reply(ctx, reply_to, order_id)

@marketplace_operator.register
async def purchase_step_22(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, seller, warehouse) = (params.get('customer'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_23', ctx.key, {'customer': customer, 'product': product, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_23(ctx: StatefulFunction, func_context, attr_17 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, warehouse) = (params.get('customer'), params.get('product'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_24', ctx.key, {'attr_17': attr_17, 'customer': customer, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_24(ctx: StatefulFunction, func_context, attr_18 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (attr_17, customer, warehouse) = (params.get('attr_17'), params.get('customer'), params.get('warehouse'))
    order_id = (
        attr_17
        + "_"
        + attr_18
        + "_"
        + str(__state__['total_transactions'])
    )
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_25', ctx.key, {'order_id': order_id, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'customer', function_name = 'add_order', key = customer, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_25(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id, warehouse) = (params.get('order_id'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_26', ctx.key, {'order_id': order_id})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'add_pending_shipment', key = warehouse, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_26(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id,) = (params.get('order_id'),)
    return send_reply(ctx, reply_to, order_id)

@marketplace_operator.register
async def purchase_step_27(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_28', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'deduct_stock', key = product, params = (quantity, reply_to))

@marketplace_operator.register
async def purchase_step_28(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_29', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_29(ctx: StatefulFunction, func_context, attr_12 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_30', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'remove_product', key = warehouse, params = (attr_12, quantity, reply_to))

@marketplace_operator.register
async def purchase_step_30(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))
    platform_fee = (final_cost * __state__['platform_fee_percent']) // 100
    seller_cut = final_cost - platform_fee
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_31', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'seller', function_name = 'credit_sale', key = seller, params = (seller_cut, reply_to))

@marketplace_operator.register
async def purchase_step_31(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))

    if final_cost > 0:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_32', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'customer', function_name = 'earn_loyalty_points', key = customer, params = (final_cost, reply_to))
    else:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_38', ctx.key, {'customer': customer, 'product': product, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_32(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_33', ctx.key, {'customer': customer, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_33(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, seller, warehouse) = (params.get('customer'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_34', ctx.key, {'customer': customer, 'product': product, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_34(ctx: StatefulFunction, func_context, attr_17 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, warehouse) = (params.get('customer'), params.get('product'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_35', ctx.key, {'attr_17': attr_17, 'customer': customer, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_35(ctx: StatefulFunction, func_context, attr_18 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (attr_17, customer, warehouse) = (params.get('attr_17'), params.get('customer'), params.get('warehouse'))
    order_id = (
        attr_17
        + "_"
        + attr_18
        + "_"
        + str(__state__['total_transactions'])
    )
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_36', ctx.key, {'order_id': order_id, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'customer', function_name = 'add_order', key = customer, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_36(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id, warehouse) = (params.get('order_id'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_37', ctx.key, {'order_id': order_id})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'add_pending_shipment', key = warehouse, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_37(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id,) = (params.get('order_id'),)
    return send_reply(ctx, reply_to, order_id)

@marketplace_operator.register
async def purchase_step_38(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, seller, warehouse) = (params.get('customer'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_39', ctx.key, {'customer': customer, 'product': product, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_39(ctx: StatefulFunction, func_context, attr_17 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, warehouse) = (params.get('customer'), params.get('product'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_40', ctx.key, {'attr_17': attr_17, 'customer': customer, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_40(ctx: StatefulFunction, func_context, attr_18 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (attr_17, customer, warehouse) = (params.get('attr_17'), params.get('customer'), params.get('warehouse'))
    order_id = (
        attr_17
        + "_"
        + attr_18
        + "_"
        + str(__state__['total_transactions'])
    )
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_41', ctx.key, {'order_id': order_id, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'customer', function_name = 'add_order', key = customer, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_41(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id, warehouse) = (params.get('order_id'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_42', ctx.key, {'order_id': order_id})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'add_pending_shipment', key = warehouse, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_42(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id,) = (params.get('order_id'),)
    return send_reply(ctx, reply_to, order_id)

@marketplace_operator.register
async def purchase_step_43(ctx: StatefulFunction, func_context, attr_8 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (base_cost, customer, discount, max_loyalty_discount, product, quantity, seller, warehouse) = (params.get('base_cost'), params.get('customer'), params.get('discount'), params.get('max_loyalty_discount'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    redeemable_points = attr_8 // 2
    potential_discount = redeemable_points * 10
    actual_discount = min(max_loyalty_discount, potential_discount)
    points_to_use = actual_discount // 10
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_44', ctx.key, {'base_cost': base_cost, 'customer': customer, 'discount': discount, 'loyalty_discount': loyalty_discount, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'redeem_loyalty_points', key = customer, params = (points_to_use, reply_to))

@marketplace_operator.register
async def purchase_step_44(ctx: StatefulFunction, func_context, loyalty_discount = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (base_cost, customer, discount, loyalty_discount, product, quantity, seller, warehouse) = (params.get('base_cost'), params.get('customer'), params.get('discount'), params.get('loyalty_discount'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    final_cost = base_cost - discount - loyalty_discount
    if final_cost < 0:
        final_cost = 0
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_45', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'deduct_funds', key = customer, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_45(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_46', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'deduct_stock', key = product, params = (quantity, reply_to))

@marketplace_operator.register
async def purchase_step_46(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_47', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_47(ctx: StatefulFunction, func_context, attr_12 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_48', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'remove_product', key = warehouse, params = (attr_12, quantity, reply_to))

@marketplace_operator.register
async def purchase_step_48(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))
    platform_fee = (final_cost * __state__['platform_fee_percent']) // 100
    seller_cut = final_cost - platform_fee
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_49', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'seller', function_name = 'credit_sale', key = seller, params = (seller_cut, reply_to))

@marketplace_operator.register
async def purchase_step_49(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))

    if final_cost > 0:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_50', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'customer', function_name = 'earn_loyalty_points', key = customer, params = (final_cost, reply_to))
    else:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_56', ctx.key, {'customer': customer, 'product': product, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_50(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_51', ctx.key, {'customer': customer, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_51(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, seller, warehouse) = (params.get('customer'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_52', ctx.key, {'customer': customer, 'product': product, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_52(ctx: StatefulFunction, func_context, attr_17 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, warehouse) = (params.get('customer'), params.get('product'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_53', ctx.key, {'attr_17': attr_17, 'customer': customer, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_53(ctx: StatefulFunction, func_context, attr_18 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (attr_17, customer, warehouse) = (params.get('attr_17'), params.get('customer'), params.get('warehouse'))
    order_id = (
        attr_17
        + "_"
        + attr_18
        + "_"
        + str(__state__['total_transactions'])
    )
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_54', ctx.key, {'order_id': order_id, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'customer', function_name = 'add_order', key = customer, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_54(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id, warehouse) = (params.get('order_id'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_55', ctx.key, {'order_id': order_id})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'add_pending_shipment', key = warehouse, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_55(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id,) = (params.get('order_id'),)
    return send_reply(ctx, reply_to, order_id)

@marketplace_operator.register
async def purchase_step_56(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, seller, warehouse) = (params.get('customer'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_57', ctx.key, {'customer': customer, 'product': product, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_57(ctx: StatefulFunction, func_context, attr_17 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, warehouse) = (params.get('customer'), params.get('product'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_58', ctx.key, {'attr_17': attr_17, 'customer': customer, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_58(ctx: StatefulFunction, func_context, attr_18 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (attr_17, customer, warehouse) = (params.get('attr_17'), params.get('customer'), params.get('warehouse'))
    order_id = (
        attr_17
        + "_"
        + attr_18
        + "_"
        + str(__state__['total_transactions'])
    )
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_59', ctx.key, {'order_id': order_id, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'customer', function_name = 'add_order', key = customer, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_59(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id, warehouse) = (params.get('order_id'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_60', ctx.key, {'order_id': order_id})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'add_pending_shipment', key = warehouse, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_60(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id,) = (params.get('order_id'),)
    return send_reply(ctx, reply_to, order_id)

@marketplace_operator.register
async def purchase_step_61(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_62', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'deduct_stock', key = product, params = (quantity, reply_to))

@marketplace_operator.register
async def purchase_step_62(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_63', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_63(ctx: StatefulFunction, func_context, attr_12 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_64', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'remove_product', key = warehouse, params = (attr_12, quantity, reply_to))

@marketplace_operator.register
async def purchase_step_64(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))
    platform_fee = (final_cost * __state__['platform_fee_percent']) // 100
    seller_cut = final_cost - platform_fee
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_65', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'seller', function_name = 'credit_sale', key = seller, params = (seller_cut, reply_to))

@marketplace_operator.register
async def purchase_step_65(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))

    if final_cost > 0:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_66', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'customer', function_name = 'earn_loyalty_points', key = customer, params = (final_cost, reply_to))
    else:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_72', ctx.key, {'customer': customer, 'product': product, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_66(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_67', ctx.key, {'customer': customer, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_67(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, seller, warehouse) = (params.get('customer'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_68', ctx.key, {'customer': customer, 'product': product, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_68(ctx: StatefulFunction, func_context, attr_17 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, warehouse) = (params.get('customer'), params.get('product'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_69', ctx.key, {'attr_17': attr_17, 'customer': customer, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_69(ctx: StatefulFunction, func_context, attr_18 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (attr_17, customer, warehouse) = (params.get('attr_17'), params.get('customer'), params.get('warehouse'))
    order_id = (
        attr_17
        + "_"
        + attr_18
        + "_"
        + str(__state__['total_transactions'])
    )
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_70', ctx.key, {'order_id': order_id, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'customer', function_name = 'add_order', key = customer, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_70(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id, warehouse) = (params.get('order_id'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_71', ctx.key, {'order_id': order_id})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'add_pending_shipment', key = warehouse, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_71(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id,) = (params.get('order_id'),)
    return send_reply(ctx, reply_to, order_id)

@marketplace_operator.register
async def purchase_step_72(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, seller, warehouse) = (params.get('customer'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_73', ctx.key, {'customer': customer, 'product': product, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_73(ctx: StatefulFunction, func_context, attr_17 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, warehouse) = (params.get('customer'), params.get('product'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_74', ctx.key, {'attr_17': attr_17, 'customer': customer, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_74(ctx: StatefulFunction, func_context, attr_18 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (attr_17, customer, warehouse) = (params.get('attr_17'), params.get('customer'), params.get('warehouse'))
    order_id = (
        attr_17
        + "_"
        + attr_18
        + "_"
        + str(__state__['total_transactions'])
    )
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_75', ctx.key, {'order_id': order_id, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'customer', function_name = 'add_order', key = customer, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_75(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id, warehouse) = (params.get('order_id'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_76', ctx.key, {'order_id': order_id})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'add_pending_shipment', key = warehouse, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_76(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id,) = (params.get('order_id'),)
    return send_reply(ctx, reply_to, order_id)

@marketplace_operator.register
async def purchase_step_77(ctx: StatefulFunction, func_context, attr_8 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (base_cost, customer, discount, max_loyalty_discount, product, quantity, seller, warehouse) = (params.get('base_cost'), params.get('customer'), params.get('discount'), params.get('max_loyalty_discount'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    redeemable_points = attr_8 // 2
    potential_discount = redeemable_points * 10
    actual_discount = min(max_loyalty_discount, potential_discount)
    points_to_use = actual_discount // 10
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_78', ctx.key, {'base_cost': base_cost, 'customer': customer, 'discount': discount, 'loyalty_discount': loyalty_discount, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'redeem_loyalty_points', key = customer, params = (points_to_use, reply_to))

@marketplace_operator.register
async def purchase_step_78(ctx: StatefulFunction, func_context, loyalty_discount = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (base_cost, customer, discount, loyalty_discount, product, quantity, seller, warehouse) = (params.get('base_cost'), params.get('customer'), params.get('discount'), params.get('loyalty_discount'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    final_cost = base_cost - discount - loyalty_discount
    if final_cost < 0:
        final_cost = 0
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_79', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'deduct_funds', key = customer, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_79(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_80', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'deduct_stock', key = product, params = (quantity, reply_to))

@marketplace_operator.register
async def purchase_step_80(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_81', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_81(ctx: StatefulFunction, func_context, attr_12 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_82', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'remove_product', key = warehouse, params = (attr_12, quantity, reply_to))

@marketplace_operator.register
async def purchase_step_82(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))
    platform_fee = (final_cost * __state__['platform_fee_percent']) // 100
    seller_cut = final_cost - platform_fee
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_83', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'seller', function_name = 'credit_sale', key = seller, params = (seller_cut, reply_to))

@marketplace_operator.register
async def purchase_step_83(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))

    if final_cost > 0:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_84', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'customer', function_name = 'earn_loyalty_points', key = customer, params = (final_cost, reply_to))
    else:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_90', ctx.key, {'customer': customer, 'product': product, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_84(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_85', ctx.key, {'customer': customer, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_85(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, seller, warehouse) = (params.get('customer'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_86', ctx.key, {'customer': customer, 'product': product, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_86(ctx: StatefulFunction, func_context, attr_17 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, warehouse) = (params.get('customer'), params.get('product'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_87', ctx.key, {'attr_17': attr_17, 'customer': customer, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_87(ctx: StatefulFunction, func_context, attr_18 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (attr_17, customer, warehouse) = (params.get('attr_17'), params.get('customer'), params.get('warehouse'))
    order_id = (
        attr_17
        + "_"
        + attr_18
        + "_"
        + str(__state__['total_transactions'])
    )
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_88', ctx.key, {'order_id': order_id, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'customer', function_name = 'add_order', key = customer, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_88(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id, warehouse) = (params.get('order_id'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_89', ctx.key, {'order_id': order_id})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'add_pending_shipment', key = warehouse, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_89(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id,) = (params.get('order_id'),)
    return send_reply(ctx, reply_to, order_id)

@marketplace_operator.register
async def purchase_step_90(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, seller, warehouse) = (params.get('customer'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_91', ctx.key, {'customer': customer, 'product': product, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_91(ctx: StatefulFunction, func_context, attr_17 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, warehouse) = (params.get('customer'), params.get('product'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_92', ctx.key, {'attr_17': attr_17, 'customer': customer, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_92(ctx: StatefulFunction, func_context, attr_18 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (attr_17, customer, warehouse) = (params.get('attr_17'), params.get('customer'), params.get('warehouse'))
    order_id = (
        attr_17
        + "_"
        + attr_18
        + "_"
        + str(__state__['total_transactions'])
    )
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_93', ctx.key, {'order_id': order_id, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'customer', function_name = 'add_order', key = customer, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_93(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id, warehouse) = (params.get('order_id'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_94', ctx.key, {'order_id': order_id})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'add_pending_shipment', key = warehouse, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_94(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id,) = (params.get('order_id'),)
    return send_reply(ctx, reply_to, order_id)

@marketplace_operator.register
async def purchase_step_95(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_96', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'deduct_stock', key = product, params = (quantity, reply_to))

@marketplace_operator.register
async def purchase_step_96(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_97', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'quantity': quantity, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_97(ctx: StatefulFunction, func_context, attr_12 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, quantity, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('quantity'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_98', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'remove_product', key = warehouse, params = (attr_12, quantity, reply_to))

@marketplace_operator.register
async def purchase_step_98(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))
    platform_fee = (final_cost * __state__['platform_fee_percent']) // 100
    seller_cut = final_cost - platform_fee
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_99', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'seller', function_name = 'credit_sale', key = seller, params = (seller_cut, reply_to))

@marketplace_operator.register
async def purchase_step_99(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))

    if final_cost > 0:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_100', ctx.key, {'customer': customer, 'final_cost': final_cost, 'product': product, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'customer', function_name = 'earn_loyalty_points', key = customer, params = (final_cost, reply_to))
    else:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_106', ctx.key, {'customer': customer, 'product': product, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_100(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final_cost, product, seller, warehouse) = (params.get('customer'), params.get('final_cost'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_101', ctx.key, {'customer': customer, 'product': product, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (final_cost, reply_to))

@marketplace_operator.register
async def purchase_step_101(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, seller, warehouse) = (params.get('customer'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_102', ctx.key, {'customer': customer, 'product': product, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_102(ctx: StatefulFunction, func_context, attr_17 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, warehouse) = (params.get('customer'), params.get('product'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_103', ctx.key, {'attr_17': attr_17, 'customer': customer, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_103(ctx: StatefulFunction, func_context, attr_18 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (attr_17, customer, warehouse) = (params.get('attr_17'), params.get('customer'), params.get('warehouse'))
    order_id = (
        attr_17
        + "_"
        + attr_18
        + "_"
        + str(__state__['total_transactions'])
    )
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_104', ctx.key, {'order_id': order_id, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'customer', function_name = 'add_order', key = customer, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_104(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id, warehouse) = (params.get('order_id'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_105', ctx.key, {'order_id': order_id})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'add_pending_shipment', key = warehouse, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_105(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id,) = (params.get('order_id'),)
    return send_reply(ctx, reply_to, order_id)

@marketplace_operator.register
async def purchase_step_106(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, seller, warehouse) = (params.get('customer'), params.get('product'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_107', ctx.key, {'customer': customer, 'product': product, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_107(ctx: StatefulFunction, func_context, attr_17 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, warehouse) = (params.get('customer'), params.get('product'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_108', ctx.key, {'attr_17': attr_17, 'customer': customer, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def purchase_step_108(ctx: StatefulFunction, func_context, attr_18 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (attr_17, customer, warehouse) = (params.get('attr_17'), params.get('customer'), params.get('warehouse'))
    order_id = (
        attr_17
        + "_"
        + attr_18
        + "_"
        + str(__state__['total_transactions'])
    )
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_109', ctx.key, {'order_id': order_id, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'customer', function_name = 'add_order', key = customer, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_109(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id, warehouse) = (params.get('order_id'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'purchase_step_110', ctx.key, {'order_id': order_id})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'add_pending_shipment', key = warehouse, params = (order_id, reply_to))

@marketplace_operator.register
async def purchase_step_110(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (order_id,) = (params.get('order_id'),)
    return send_reply(ctx, reply_to, order_id)



@marketplace_operator.register
async def batch_restock(
    ctx: StatefulFunction, products: list[str],
    quantities: list[int],
    warehouse: str,
reply_to: list = None) -> str:
    restocked = 0
    skipped = 0
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'batch_restock_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'restocked': restocked, 'skipped': skipped, 'warehouse': warehouse}, None, reply_to))

@marketplace_operator.register
async def batch_restock_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, quantities, restocked, skipped, warehouse) = (params.get('__loop_index_1'), params.get('products'), params.get('quantities'), params.get('restocked'), params.get('skipped'), params.get('warehouse'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'batch_restock_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'restocked': restocked, 'skipped': skipped, 'warehouse': warehouse}, None, reply_to))
    else:
        i = __loop_index_1
        __loop_index_1 += 1
        p = products[i]
        qty = quantities[i]
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'batch_restock_step_4', ctx.key, {'__loop_index_1': __loop_index_1, 'p': p, 'products': products, 'qty': qty, 'quantities': quantities, 'restocked': restocked, 'skipped': skipped, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'warehouse', function_name = 'get_available_capacity', key = warehouse, params = (reply_to,))

@marketplace_operator.register
async def batch_restock_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, quantities, restocked, skipped, warehouse) = (params.get('__loop_index_1'), params.get('products'), params.get('quantities'), params.get('restocked'), params.get('skipped'), params.get('warehouse'))
    return send_reply(ctx, reply_to, "Restocked: " + str(restocked) + ", Skipped: " + str(skipped))

@marketplace_operator.register
async def batch_restock_step_4(ctx: StatefulFunction, func_context, available_space = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, p, products, qty, quantities, restocked, skipped, warehouse) = (params.get('__loop_index_1'), params.get('p'), params.get('products'), params.get('qty'), params.get('quantities'), params.get('restocked'), params.get('skipped'), params.get('warehouse'))

    if available_space >= qty:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'batch_restock_step_5', ctx.key, {'__loop_index_1': __loop_index_1, 'p': p, 'products': products, 'qty': qty, 'quantities': quantities, 'restocked': restocked, 'skipped': skipped, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'product', function_name = 'add_stock', key = p, params = (qty, reply_to))
    else:
        skipped += 1
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'batch_restock_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'restocked': restocked, 'skipped': skipped, 'warehouse': warehouse}, None, reply_to))

@marketplace_operator.register
async def batch_restock_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, p, products, qty, quantities, restocked, skipped, warehouse) = (params.get('__loop_index_1'), params.get('p'), params.get('products'), params.get('qty'), params.get('quantities'), params.get('restocked'), params.get('skipped'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'batch_restock_step_6', ctx.key, {'__loop_index_1': __loop_index_1, 'products': products, 'qty': qty, 'quantities': quantities, 'restocked': restocked, 'skipped': skipped, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = p, params = (reply_to,))

@marketplace_operator.register
async def batch_restock_step_6(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, qty, quantities, restocked, skipped, warehouse) = (params.get('__loop_index_1'), params.get('products'), params.get('qty'), params.get('quantities'), params.get('restocked'), params.get('skipped'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'batch_restock_step_7', ctx.key, {'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'restocked': restocked, 'skipped': skipped, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'store_product', key = warehouse, params = (attr_3, qty, reply_to))

@marketplace_operator.register
async def batch_restock_step_7(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, quantities, restocked, skipped, warehouse) = (params.get('__loop_index_1'), params.get('products'), params.get('quantities'), params.get('restocked'), params.get('skipped'), params.get('warehouse'))
    restocked += 1
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'batch_restock_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'restocked': restocked, 'skipped': skipped, 'warehouse': warehouse}, None, reply_to))


@marketplace_operator.register
async def compute_cart_total(
    ctx: StatefulFunction, customer: str,
    products: list[str],
    quantities: list[int],
reply_to: list = None) -> int:
    total = 0
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'compute_cart_total_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'total': total}, None, reply_to))

@marketplace_operator.register
async def compute_cart_total_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, quantities, total) = (params.get('__loop_index_1'), params.get('products'), params.get('quantities'), params.get('total'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'compute_cart_total_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'total': total}, None, reply_to))
    else:
        i = __loop_index_1
        __loop_index_1 += 1
        p = products[i]
        qty = quantities[i]
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'compute_cart_total_step_4', ctx.key, {'__loop_index_1': __loop_index_1, 'products': products, 'qty': qty, 'quantities': quantities, 'total': total})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_price', key = p, params = (reply_to,))

@marketplace_operator.register
async def compute_cart_total_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, quantities, total) = (params.get('__loop_index_1'), params.get('products'), params.get('quantities'), params.get('total'))
    return send_reply(ctx, reply_to, total)

@marketplace_operator.register
async def compute_cart_total_step_4(ctx: StatefulFunction, func_context, price = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, qty, quantities, total) = (params.get('__loop_index_1'), params.get('products'), params.get('qty'), params.get('quantities'), params.get('total'))

    if qty <= 5:
        item_total = qty * price
    elif qty <= 20:
        item_total = 5 * price + int((qty - 5) * price * 0.9)
    else:
        item_total = (
            5 * price
            + int(15 * price * 0.9)
            + int((qty - 20) * price * 0.8)
        )
    total += item_total
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'compute_cart_total_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'total': total}, None, reply_to))


@marketplace_operator.register
async def submit_review(
    ctx: StatefulFunction, customer: str,
    product: str,
    score: int,
reply_to: list = None) -> int:
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'submit_review_step_2', ctx.key, {'customer': customer, 'product': product, 'score': score})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def submit_review_step_2(ctx: StatefulFunction, func_context, product_id = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, product, score) = (params.get('customer'), params.get('product'), params.get('score'))
    _comp_result_1 = []
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'submit_review_step_3', ctx.key, {'_comp_result_1': _comp_result_1, 'customer': customer, 'product': product, 'product_id': product_id, 'score': score})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'get_order_history', key = customer, params = (reply_to,))

@marketplace_operator.register
async def submit_review_step_3(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (_comp_result_1, customer, product, product_id, score) = (params.get('_comp_result_1'), params.get('customer'), params.get('product'), params.get('product_id'), params.get('score'))
    for order_id in attr_3:
        _comp_result_1.append(product_id in order_id)
    has_purchased = any(_comp_result_1)
    if not has_purchased:
        raise Exception("Customer cannot review a product they haven't purchased.")
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'submit_review_step_4', ctx.key, {'_comp_result_1': _comp_result_1, 'customer': customer, 'product': product, 'score': score})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def submit_review_step_4(ctx: StatefulFunction, func_context, attr_4 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (_comp_result_1, customer, product, score) = (params.get('_comp_result_1'), params.get('customer'), params.get('product'), params.get('score'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'submit_review_step_5', ctx.key, {'_comp_result_1': _comp_result_1, 'customer': customer, 'product': product, 'score': score})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'mark_reviewed', key = customer, params = (attr_4, reply_to))

@marketplace_operator.register
async def submit_review_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (_comp_result_1, customer, product, score) = (params.get('_comp_result_1'), params.get('customer'), params.get('product'), params.get('score'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'submit_review_step_6', ctx.key, {'_comp_result_1': _comp_result_1, 'customer': customer})
    ctx.call_remote_async(operator_name = 'product', function_name = 'add_rating', key = product, params = (score, reply_to))

@marketplace_operator.register
async def submit_review_step_6(ctx: StatefulFunction, func_context, new_avg = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (_comp_result_1, customer) = (params.get('_comp_result_1'), params.get('customer'))
    ctx.call_remote_async(operator_name = 'customer', function_name = 'earn_loyalty_points', key = customer, params = (20, [{'sink': True}]))
    return send_reply(ctx, reply_to, new_avg)


@marketplace_operator.register
async def get_top_product_scores(ctx: StatefulFunction, products: list[str], reply_to: list = None) -> list[int]:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_top_product_scores_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products}, None, reply_to))

@marketplace_operator.register
async def get_top_product_scores_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_top_product_scores_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products}, None, reply_to))
    else:
        p = products[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'get_top_product_scores_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_popularity_score', key = p, params = (reply_to,))

@marketplace_operator.register
async def get_top_product_scores_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'))
    scores = _comp_result_1
    return send_reply(ctx, reply_to, scores)

@marketplace_operator.register
async def get_top_product_scores_step_4(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'))
    _comp_result_1.append(attr_1)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_top_product_scores_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products}, None, reply_to))


@marketplace_operator.register
async def get_affordable_products(
    ctx: StatefulFunction, products: list[str], budget: int, 
reply_to: list = None) -> list[str]:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_affordable_products_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'budget': budget, 'products': products}, None, reply_to))

@marketplace_operator.register
async def get_affordable_products_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, budget, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('budget'), params.get('products'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_affordable_products_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'budget': budget, 'products': products}, None, reply_to))
    else:
        p = products[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'get_affordable_products_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'budget': budget, 'p': p, 'products': products})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_price', key = p, params = (reply_to,))

@marketplace_operator.register
async def get_affordable_products_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, budget, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('budget'), params.get('products'))
    affordable = _comp_result_1
    return send_reply(ctx, reply_to, affordable)

@marketplace_operator.register
async def get_affordable_products_step_4(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, budget, p, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('budget'), params.get('p'), params.get('products'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'get_affordable_products_step_5', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'attr_2': attr_2, 'budget': budget, 'p': p, 'products': products})
    ctx.call_remote_async(operator_name = 'product', function_name = 'is_available', key = p, params = (reply_to,))

@marketplace_operator.register
async def get_affordable_products_step_5(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, attr_2, budget, p, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('attr_2'), params.get('budget'), params.get('p'), params.get('products'))
    if (attr_2 <= budget) & attr_3:
        _comp_result_1.append(p)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_affordable_products_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'budget': budget, 'products': products}, None, reply_to))


@marketplace_operator.register
async def total_wishlist_value(ctx: StatefulFunction, customer: str, products: list[str], reply_to: list = None) -> int:
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'total_wishlist_value_step_2', ctx.key, {'products': products})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'get_wishlist', key = customer, params = (reply_to,))

@marketplace_operator.register
async def total_wishlist_value_step_2(ctx: StatefulFunction, func_context, wishlist = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (products,) = (params.get('products'),)
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'total_wishlist_value_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products, 'wishlist': wishlist}, None, reply_to))

@marketplace_operator.register
async def total_wishlist_value_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products, wishlist) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'), params.get('wishlist'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'total_wishlist_value_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products, 'wishlist': wishlist}, None, reply_to))
    else:
        p = products[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'total_wishlist_value_step_5', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'p': p, 'products': products, 'wishlist': wishlist})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = p, params = (reply_to,))

@marketplace_operator.register
async def total_wishlist_value_step_4(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products, wishlist) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'), params.get('wishlist'))
    total = sum(_comp_result_1)
    return send_reply(ctx, reply_to, total)

@marketplace_operator.register
async def total_wishlist_value_step_5(ctx: StatefulFunction, func_context, attr_4 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, p, products, wishlist) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('p'), params.get('products'), params.get('wishlist'))
    if attr_4 in wishlist:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'total_wishlist_value_step_6', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products, 'wishlist': wishlist})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_price', key = p, params = (reply_to,))
    else:
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'total_wishlist_value_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products, 'wishlist': wishlist}, None, reply_to))

@marketplace_operator.register
async def total_wishlist_value_step_6(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products, wishlist) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'), params.get('wishlist'))
    _comp_result_1.append(attr_2)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'total_wishlist_value_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products, 'wishlist': wishlist}, None, reply_to))


@marketplace_operator.register
async def suspend_seller_and_deactivate_products(
    ctx: StatefulFunction, seller: str,
    products: list[str],
reply_to: list = None) -> str:
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'suspend_seller_and_deactivate_products_step_2', ctx.key, {'products': products, 'seller': seller})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'debit_penalty', key = seller, params = (500, reply_to))

@marketplace_operator.register
async def suspend_seller_and_deactivate_products_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (products, seller) = (params.get('products'), params.get('seller'))
    deactivated = 0
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'suspend_seller_and_deactivate_products_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'deactivated': deactivated, 'products': products, 'seller': seller}, None, reply_to))

@marketplace_operator.register
async def suspend_seller_and_deactivate_products_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, deactivated, products, seller) = (params.get('__loop_index_1'), params.get('deactivated'), params.get('products'), params.get('seller'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'suspend_seller_and_deactivate_products_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'deactivated': deactivated, 'products': products, 'seller': seller}, None, reply_to))
    else:
        p = products[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'suspend_seller_and_deactivate_products_step_6', ctx.key, {'__loop_index_1': __loop_index_1, 'deactivated': deactivated, 'p': p, 'products': products, 'seller': seller})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_seller', key = p, params = (reply_to,))

@marketplace_operator.register
async def suspend_seller_and_deactivate_products_step_4(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, deactivated, products, seller) = (params.get('__loop_index_1'), params.get('deactivated'), params.get('products'), params.get('seller'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'suspend_seller_and_deactivate_products_step_5', ctx.key, {'__loop_index_1': __loop_index_1, 'deactivated': deactivated})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def suspend_seller_and_deactivate_products_step_5(ctx: StatefulFunction, func_context, attr_4 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, deactivated) = (params.get('__loop_index_1'), params.get('deactivated'))
    return send_reply(ctx, reply_to, "Deactivated " + str(deactivated) + " products for seller " + attr_4)

@marketplace_operator.register
async def suspend_seller_and_deactivate_products_step_6(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, deactivated, p, products, seller) = (params.get('__loop_index_1'), params.get('deactivated'), params.get('p'), params.get('products'), params.get('seller'))
    if (attr_3 == seller):
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'suspend_seller_and_deactivate_products_step_7', ctx.key, {'__loop_index_1': __loop_index_1, 'deactivated': deactivated, 'products': products, 'seller': seller})
        ctx.call_remote_async(operator_name = 'product', function_name = 'deactivate', key = p, params = (reply_to,))
    else:
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'suspend_seller_and_deactivate_products_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'deactivated': deactivated, 'products': products, 'seller': seller}, None, reply_to))

@marketplace_operator.register
async def suspend_seller_and_deactivate_products_step_7(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, deactivated, products, seller) = (params.get('__loop_index_1'), params.get('deactivated'), params.get('products'), params.get('seller'))
    deactivated += 1
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'suspend_seller_and_deactivate_products_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'deactivated': deactivated, 'products': products, 'seller': seller}, None, reply_to))


@marketplace_operator.register
async def restock_and_report(
    ctx: StatefulFunction, seller: str,
    products: list[str],
    quantities: list[int],
    warehouse: str,
reply_to: list = None) -> dict[str, int]:
    total_units = 0
    total_fee = 0
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'restock_and_report_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'seller': seller, 'total_fee': total_fee, 'total_units': total_units, 'warehouse': warehouse}, None, reply_to))

@marketplace_operator.register
async def restock_and_report_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, quantities, seller, total_fee, total_units, warehouse) = (params.get('__loop_index_1'), params.get('products'), params.get('quantities'), params.get('seller'), params.get('total_fee'), params.get('total_units'), params.get('warehouse'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'restock_and_report_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'seller': seller, 'total_fee': total_fee, 'total_units': total_units, 'warehouse': warehouse}, None, reply_to))
    else:
        i = __loop_index_1
        __loop_index_1 += 1
        p = products[i]
        qty = quantities[i]
        fee = qty * 2
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'restock_and_report_step_4', ctx.key, {'__loop_index_1': __loop_index_1, 'fee': fee, 'p': p, 'products': products, 'qty': qty, 'quantities': quantities, 'seller': seller, 'total_fee': total_fee, 'total_units': total_units, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'warehouse', function_name = 'get_available_capacity', key = warehouse, params = (reply_to,))

@marketplace_operator.register
async def restock_and_report_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, quantities, seller, total_fee, total_units, warehouse) = (params.get('__loop_index_1'), params.get('products'), params.get('quantities'), params.get('seller'), params.get('total_fee'), params.get('total_units'), params.get('warehouse'))
    return send_reply(ctx, reply_to, {"units_restocked": total_units, "fees_charged": total_fee})

@marketplace_operator.register
async def restock_and_report_step_4(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, fee, p, products, qty, quantities, seller, total_fee, total_units, warehouse) = (params.get('__loop_index_1'), params.get('fee'), params.get('p'), params.get('products'), params.get('qty'), params.get('quantities'), params.get('seller'), params.get('total_fee'), params.get('total_units'), params.get('warehouse'))

    if attr_1 < qty:
        raise WarehouseCapacityExceeded("Cannot restock: warehouse is full.")
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'restock_and_report_step_5', ctx.key, {'__loop_index_1': __loop_index_1, 'fee': fee, 'p': p, 'products': products, 'qty': qty, 'quantities': quantities, 'seller': seller, 'total_fee': total_fee, 'total_units': total_units, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'withdraw', key = seller, params = (fee, reply_to))

@marketplace_operator.register
async def restock_and_report_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, fee, p, products, qty, quantities, seller, total_fee, total_units, warehouse) = (params.get('__loop_index_1'), params.get('fee'), params.get('p'), params.get('products'), params.get('qty'), params.get('quantities'), params.get('seller'), params.get('total_fee'), params.get('total_units'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'restock_and_report_step_6', ctx.key, {'__loop_index_1': __loop_index_1, 'fee': fee, 'p': p, 'products': products, 'qty': qty, 'quantities': quantities, 'seller': seller, 'total_fee': total_fee, 'total_units': total_units, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'add_stock', key = p, params = (qty, reply_to))

@marketplace_operator.register
async def restock_and_report_step_6(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, fee, p, products, qty, quantities, seller, total_fee, total_units, warehouse) = (params.get('__loop_index_1'), params.get('fee'), params.get('p'), params.get('products'), params.get('qty'), params.get('quantities'), params.get('seller'), params.get('total_fee'), params.get('total_units'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'restock_and_report_step_7', ctx.key, {'__loop_index_1': __loop_index_1, 'fee': fee, 'products': products, 'qty': qty, 'quantities': quantities, 'seller': seller, 'total_fee': total_fee, 'total_units': total_units, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = p, params = (reply_to,))

@marketplace_operator.register
async def restock_and_report_step_7(ctx: StatefulFunction, func_context, attr_4 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, fee, products, qty, quantities, seller, total_fee, total_units, warehouse) = (params.get('__loop_index_1'), params.get('fee'), params.get('products'), params.get('qty'), params.get('quantities'), params.get('seller'), params.get('total_fee'), params.get('total_units'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'restock_and_report_step_8', ctx.key, {'__loop_index_1': __loop_index_1, 'fee': fee, 'products': products, 'qty': qty, 'quantities': quantities, 'seller': seller, 'total_fee': total_fee, 'total_units': total_units, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'store_product', key = warehouse, params = (attr_4, qty, reply_to))

@marketplace_operator.register
async def restock_and_report_step_8(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, fee, products, qty, quantities, seller, total_fee, total_units, warehouse) = (params.get('__loop_index_1'), params.get('fee'), params.get('products'), params.get('qty'), params.get('quantities'), params.get('seller'), params.get('total_fee'), params.get('total_units'), params.get('warehouse'))
    total_units += qty
    total_fee += fee
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'restock_and_report_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'seller': seller, 'total_fee': total_fee, 'total_units': total_units, 'warehouse': warehouse}, None, reply_to))


@marketplace_operator.register
async def process_bulk_orders(
    ctx: StatefulFunction, customers: list[str],
    product: str,
    quantity_each: int,
    warehouse: str,
reply_to: list = None) -> str:

    if quantity_each <= 0:
        raise ValueError("Quantity must be positive.")
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_2', ctx.key, {'customers': customers, 'product': product, 'quantity_each': quantity_each, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_seller', key = product, params = (reply_to,))

@marketplace_operator.register
async def process_bulk_orders_step_2(ctx: StatefulFunction, func_context, seller = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customers, product, quantity_each, warehouse) = (params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('warehouse'))
    success_count = 0
    skip_count = 0
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_3', ctx.key, {'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_price', key = product, params = (reply_to,))

@marketplace_operator.register
async def process_bulk_orders_step_3(ctx: StatefulFunction, func_context, unit_price = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    cost = unit_price * quantity_each
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'process_bulk_orders_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cost': cost, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse}, None, reply_to))

@marketplace_operator.register
async def process_bulk_orders_step_4(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cost, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('cost'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    if __loop_index_1 >= len(customers):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'process_bulk_orders_step_5', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cost': cost, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse}, None, reply_to))
    else:
        customer = customers[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_6', ctx.key, {'__loop_index_1': __loop_index_1, 'cost': cost, 'customer': customer, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'customer', function_name = 'get_balance', key = customer, params = (reply_to,))

@marketplace_operator.register
async def process_bulk_orders_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cost, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('cost'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    return send_reply(ctx, reply_to, f"Bulk orders done. Success: {success_count}, Skipped: {skip_count}")

@marketplace_operator.register
async def process_bulk_orders_step_6(ctx: StatefulFunction, func_context, attr_10 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cost, customer, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('cost'), params.get('customer'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_7', ctx.key, {'__loop_index_1': __loop_index_1, 'attr_10': attr_10, 'cost': cost, 'customer': customer, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_stock', key = product, params = (reply_to,))

@marketplace_operator.register
async def process_bulk_orders_step_7(ctx: StatefulFunction, func_context, attr_11 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, attr_10, cost, customer, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('attr_10'), params.get('cost'), params.get('customer'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_8', ctx.key, {'__loop_index_1': __loop_index_1, 'attr_10': attr_10, 'attr_11': attr_11, 'cost': cost, 'customer': customer, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'is_available', key = product, params = (reply_to,))

@marketplace_operator.register
async def process_bulk_orders_step_8(ctx: StatefulFunction, func_context, attr_12 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, attr_10, attr_11, cost, customer, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('attr_10'), params.get('attr_11'), params.get('cost'), params.get('customer'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_9', ctx.key, {'__loop_index_1': __loop_index_1, 'attr_10': attr_10, 'attr_11': attr_11, 'attr_12': attr_12, 'cost': cost, 'customer': customer, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'is_active', key = seller, params = (reply_to,))

@marketplace_operator.register
async def process_bulk_orders_step_9(ctx: StatefulFunction, func_context, attr_13 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, attr_10, attr_11, attr_12, cost, customer, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('attr_10'), params.get('attr_11'), params.get('attr_12'), params.get('cost'), params.get('customer'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_10', ctx.key, {'__loop_index_1': __loop_index_1, 'attr_10': attr_10, 'attr_11': attr_11, 'attr_12': attr_12, 'attr_13': attr_13, 'cost': cost, 'customer': customer, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def process_bulk_orders_step_10(ctx: StatefulFunction, func_context, attr_14 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, attr_10, attr_11, attr_12, attr_13, cost, customer, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('attr_10'), params.get('attr_11'), params.get('attr_12'), params.get('attr_13'), params.get('cost'), params.get('customer'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_11', ctx.key, {'__loop_index_1': __loop_index_1, 'attr_10': attr_10, 'attr_11': attr_11, 'attr_12': attr_12, 'attr_13': attr_13, 'cost': cost, 'customer': customer, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'get_product_quantity', key = warehouse, params = (attr_14, reply_to))

@marketplace_operator.register
async def process_bulk_orders_step_11(ctx: StatefulFunction, func_context, attr_15 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, attr_10, attr_11, attr_12, attr_13, cost, customer, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('attr_10'), params.get('attr_11'), params.get('attr_12'), params.get('attr_13'), params.get('cost'), params.get('customer'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))

    if (
        (attr_10 >= cost)
        & (attr_11 >= quantity_each)
        & attr_12
        & attr_13
        & (attr_15 >= quantity_each)
    ):
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_12', ctx.key, {'__loop_index_1': __loop_index_1, 'cost': cost, 'customer': customer, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'customer', function_name = 'deduct_funds', key = customer, params = (cost, reply_to))
    else:
        skip_count += 1
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'process_bulk_orders_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cost': cost, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse}, None, reply_to))

@marketplace_operator.register
async def process_bulk_orders_step_12(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cost, customer, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('cost'), params.get('customer'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_13', ctx.key, {'__loop_index_1': __loop_index_1, 'cost': cost, 'customer': customer, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'deduct_stock', key = product, params = (quantity_each, reply_to))

@marketplace_operator.register
async def process_bulk_orders_step_13(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cost, customer, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('cost'), params.get('customer'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_14', ctx.key, {'__loop_index_1': __loop_index_1, 'cost': cost, 'customer': customer, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = product, params = (reply_to,))

@marketplace_operator.register
async def process_bulk_orders_step_14(ctx: StatefulFunction, func_context, attr_5 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cost, customer, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('cost'), params.get('customer'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_15', ctx.key, {'__loop_index_1': __loop_index_1, 'cost': cost, 'customer': customer, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'remove_product', key = warehouse, params = (attr_5, quantity_each, reply_to))

@marketplace_operator.register
async def process_bulk_orders_step_15(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cost, customer, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('cost'), params.get('customer'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    platform_fee = (cost * __state__['platform_fee_percent']) // 100
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_16', ctx.key, {'__loop_index_1': __loop_index_1, 'cost': cost, 'customer': customer, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'seller', function_name = 'credit_sale', key = seller, params = (cost - platform_fee, reply_to))

@marketplace_operator.register
async def process_bulk_orders_step_16(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cost, customer, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('cost'), params.get('customer'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_17', ctx.key, {'__loop_index_1': __loop_index_1, 'cost': cost, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'earn_loyalty_points', key = customer, params = (cost, reply_to))

@marketplace_operator.register
async def process_bulk_orders_step_17(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cost, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('cost'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'process_bulk_orders_step_18', ctx.key, {'__loop_index_1': __loop_index_1, 'cost': cost, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (cost, reply_to))

@marketplace_operator.register
async def process_bulk_orders_step_18(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, cost, customers, product, quantity_each, seller, skip_count, success_count, warehouse) = (params.get('__loop_index_1'), params.get('cost'), params.get('customers'), params.get('product'), params.get('quantity_each'), params.get('seller'), params.get('skip_count'), params.get('success_count'), params.get('warehouse'))
    success_count += 1
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'process_bulk_orders_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'cost': cost, 'customers': customers, 'product': product, 'quantity_each': quantity_each, 'seller': seller, 'skip_count': skip_count, 'success_count': success_count, 'warehouse': warehouse}, None, reply_to))


@marketplace_operator.register
async def warehouse_health_check(ctx: StatefulFunction, warehouses: list[str], reply_to: list = None) -> list[int]:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'warehouse_health_check_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'warehouses': warehouses}, None, reply_to))

@marketplace_operator.register
async def warehouse_health_check_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, warehouses) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('warehouses'))
    if __loop_index_1 >= len(warehouses):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'warehouse_health_check_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'warehouses': warehouses}, None, reply_to))
    else:
        w = warehouses[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'warehouse_health_check_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'warehouses': warehouses})
        ctx.call_remote_async(operator_name = 'warehouse', function_name = 'calculate_fill_rate', key = w, params = (reply_to,))

@marketplace_operator.register
async def warehouse_health_check_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, warehouses) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('warehouses'))
    return send_reply(ctx, reply_to, _comp_result_1)

@marketplace_operator.register
async def warehouse_health_check_step_4(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, warehouses) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('warehouses'))
    _comp_result_1.append(attr_1)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'warehouse_health_check_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'warehouses': warehouses}, None, reply_to))


@marketplace_operator.register
async def find_overstocked_warehouses(
    ctx: StatefulFunction, warehouses: list[str], threshold: int, 
reply_to: list = None) -> list[str]:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'find_overstocked_warehouses_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'threshold': threshold, 'warehouses': warehouses}, None, reply_to))

@marketplace_operator.register
async def find_overstocked_warehouses_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, threshold, warehouses) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('threshold'), params.get('warehouses'))
    if __loop_index_1 >= len(warehouses):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'find_overstocked_warehouses_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'threshold': threshold, 'warehouses': warehouses}, None, reply_to))
    else:
        w = warehouses[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'find_overstocked_warehouses_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'threshold': threshold, 'w': w, 'warehouses': warehouses})
        ctx.call_remote_async(operator_name = 'warehouse', function_name = 'calculate_fill_rate', key = w, params = (reply_to,))

@marketplace_operator.register
async def find_overstocked_warehouses_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, threshold, warehouses) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('threshold'), params.get('warehouses'))
    return send_reply(ctx, reply_to, _comp_result_1)

@marketplace_operator.register
async def find_overstocked_warehouses_step_4(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, threshold, w, warehouses) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('threshold'), params.get('w'), params.get('warehouses'))
    if attr_2 > threshold:
        _comp_result_1.append(w)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'find_overstocked_warehouses_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'threshold': threshold, 'warehouses': warehouses}, None, reply_to))


@marketplace_operator.register
async def seller_revenue_summary(ctx: StatefulFunction, sellers: list[str], reply_to: list = None) -> dict[str, int]:
    _comp_result_1 = {}
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'seller_revenue_summary_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'sellers': sellers}, None, reply_to))

@marketplace_operator.register
async def seller_revenue_summary_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, sellers) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('sellers'))
    if __loop_index_1 >= len(sellers):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'seller_revenue_summary_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'sellers': sellers}, None, reply_to))
    else:
        s = sellers[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'seller_revenue_summary_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 's': s, 'sellers': sellers})
        ctx.call_remote_async(operator_name = 'seller', function_name = 'get_revenue', key = s, params = (reply_to,))

@marketplace_operator.register
async def seller_revenue_summary_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, sellers) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('sellers'))
    return send_reply(ctx, reply_to, _comp_result_1)

@marketplace_operator.register
async def seller_revenue_summary_step_4(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, s, sellers) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('s'), params.get('sellers'))
    _comp_result_1[s] = attr_1
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'seller_revenue_summary_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'sellers': sellers}, None, reply_to))


@marketplace_operator.register
async def rank_products_by_popularity(ctx: StatefulFunction, products: list[str], reply_to: list = None) -> list[int]:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'rank_products_by_popularity_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products}, None, reply_to))

@marketplace_operator.register
async def rank_products_by_popularity_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'rank_products_by_popularity_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products}, None, reply_to))
    else:
        p = products[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'rank_products_by_popularity_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_popularity_score', key = p, params = (reply_to,))

@marketplace_operator.register
async def rank_products_by_popularity_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'))
    scores = _comp_result_1
    scores.sort(reverse=True)
    return send_reply(ctx, reply_to, scores)

@marketplace_operator.register
async def rank_products_by_popularity_step_4(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'))
    _comp_result_1.append(attr_1)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'rank_products_by_popularity_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products}, None, reply_to))


@marketplace_operator.register
async def total_platform_earnings_from_sellers(ctx: StatefulFunction, sellers: list[str], reply_to: list = None) -> int:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'total_platform_earnings_from_sellers_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'sellers': sellers}, None, reply_to))

@marketplace_operator.register
async def total_platform_earnings_from_sellers_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, sellers) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('sellers'))
    if __loop_index_1 >= len(sellers):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'total_platform_earnings_from_sellers_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'sellers': sellers}, None, reply_to))
    else:
        s = sellers[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'total_platform_earnings_from_sellers_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'sellers': sellers})
        ctx.call_remote_async(operator_name = 'seller', function_name = 'get_revenue', key = s, params = (reply_to,))

@marketplace_operator.register
async def total_platform_earnings_from_sellers_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, sellers) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('sellers'))
    total = sum(
        _comp_result_1
    )
    return send_reply(ctx, reply_to, total)

@marketplace_operator.register
async def total_platform_earnings_from_sellers_step_4(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    __state__ = ctx.get() or {}
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, sellers) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('sellers'))
    _comp_result_1.append((attr_1 * __state__['platform_fee_percent']) // 100)
    ctx.put(__state__)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'total_platform_earnings_from_sellers_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'sellers': sellers}, None, reply_to))



@marketplace_operator.register
async def multi_product_availability_check(
    ctx: StatefulFunction, products: list[str], quantities: list[int], 
reply_to: list = None) -> bool:
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'multi_product_availability_check_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities}, None, reply_to))

@marketplace_operator.register
async def multi_product_availability_check_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, quantities) = (params.get('__loop_index_1'), params.get('products'), params.get('quantities'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'multi_product_availability_check_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities}, None, reply_to))
    else:
        i = __loop_index_1
        __loop_index_1 += 1
        p = products[i]
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'multi_product_availability_check_step_4', ctx.key, {'__loop_index_1': __loop_index_1, 'i': i, 'p': p, 'products': products, 'quantities': quantities})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_stock', key = p, params = (reply_to,))

@marketplace_operator.register
async def multi_product_availability_check_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, quantities) = (params.get('__loop_index_1'), params.get('products'), params.get('quantities'))
    return send_reply(ctx, reply_to, True)

@marketplace_operator.register
async def multi_product_availability_check_step_4(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, i, p, products, quantities) = (params.get('__loop_index_1'), params.get('i'), params.get('p'), params.get('products'), params.get('quantities'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'multi_product_availability_check_step_5', ctx.key, {'__loop_index_1': __loop_index_1, 'attr_1': attr_1, 'i': i, 'p': p, 'products': products, 'quantities': quantities})
    ctx.call_remote_async(operator_name = 'product', function_name = 'is_available', key = p, params = (reply_to,))

@marketplace_operator.register
async def multi_product_availability_check_step_5(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, attr_1, i, p, products, quantities) = (params.get('__loop_index_1'), params.get('attr_1'), params.get('i'), params.get('p'), params.get('products'), params.get('quantities'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'multi_product_availability_check_step_6', ctx.key, {'__loop_index_1': __loop_index_1, 'attr_1': attr_1, 'attr_2': attr_2, 'i': i, 'products': products, 'quantities': quantities})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_seller', key = p, params = (reply_to,))

@marketplace_operator.register
async def multi_product_availability_check_step_6(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, attr_1, attr_2, i, products, quantities) = (params.get('__loop_index_1'), params.get('attr_1'), params.get('attr_2'), params.get('i'), params.get('products'), params.get('quantities'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'multi_product_availability_check_step_7', ctx.key, {'__loop_index_1': __loop_index_1, 'attr_1': attr_1, 'attr_2': attr_2, 'i': i, 'products': products, 'quantities': quantities})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'is_active', key = attr_3, params = (reply_to,))

@marketplace_operator.register
async def multi_product_availability_check_step_7(ctx: StatefulFunction, func_context, attr_4 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, attr_1, attr_2, i, products, quantities) = (params.get('__loop_index_1'), params.get('attr_1'), params.get('attr_2'), params.get('i'), params.get('products'), params.get('quantities'))
    if (
        (attr_1 < quantities[i])
        | (not attr_2)
        | (not attr_4)
    ):
        return send_reply(ctx, reply_to, False)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'multi_product_availability_check_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities}, None, reply_to))


@marketplace_operator.register
async def compute_order_breakdown(
    ctx: StatefulFunction, products: list[str],
    quantities: list[int],
    coupon: str,
reply_to: list = None) -> tuple[int, int, int]:
    subtotal = 0
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'compute_order_breakdown_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'coupon': coupon, 'products': products, 'quantities': quantities, 'subtotal': subtotal}, None, reply_to))

@marketplace_operator.register
async def compute_order_breakdown_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, coupon, products, quantities, subtotal) = (params.get('__loop_index_1'), params.get('coupon'), params.get('products'), params.get('quantities'), params.get('subtotal'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'compute_order_breakdown_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'coupon': coupon, 'products': products, 'quantities': quantities, 'subtotal': subtotal}, None, reply_to))
    else:
        i = __loop_index_1
        __loop_index_1 += 1
        attr_1 = products[i]
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'compute_order_breakdown_step_5', ctx.key, {'__loop_index_1': __loop_index_1, 'coupon': coupon, 'i': i, 'products': products, 'quantities': quantities, 'subtotal': subtotal})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_price', key = attr_1, params = (reply_to,))

@marketplace_operator.register
async def compute_order_breakdown_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, coupon, products, quantities, subtotal) = (params.get('__loop_index_1'), params.get('coupon'), params.get('products'), params.get('quantities'), params.get('subtotal'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'compute_order_breakdown_step_4', ctx.key, {'__loop_index_1': __loop_index_1, 'subtotal': subtotal})
    ctx.call_remote_async(operator_name = 'coupon', function_name = 'apply', key = coupon, params = (subtotal, reply_to))

@marketplace_operator.register
async def compute_order_breakdown_step_4(ctx: StatefulFunction, func_context, discount = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, subtotal) = (params.get('__loop_index_1'), params.get('subtotal'))
    final = subtotal - discount
    return send_reply(ctx, reply_to, (subtotal, discount, final))

@marketplace_operator.register
async def compute_order_breakdown_step_5(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, coupon, i, products, quantities, subtotal) = (params.get('__loop_index_1'), params.get('coupon'), params.get('i'), params.get('products'), params.get('quantities'), params.get('subtotal'))
    subtotal += attr_2 * quantities[i]
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'compute_order_breakdown_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'coupon': coupon, 'products': products, 'quantities': quantities, 'subtotal': subtotal}, None, reply_to))


@marketplace_operator.register
async def loyalty_cashback_campaign(
    ctx: StatefulFunction, customers: list[str], products: list[str], 
reply_to: list = None) -> int:
    active_count = 0
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'loyalty_cashback_campaign_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'active_count': active_count, 'customers': customers, 'products': products}, None, reply_to))

@marketplace_operator.register
async def loyalty_cashback_campaign_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, active_count, customers, products) = (params.get('__loop_index_1'), params.get('active_count'), params.get('customers'), params.get('products'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'loyalty_cashback_campaign_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'active_count': active_count, 'customers': customers, 'products': products}, None, reply_to))
    else:
        p = products[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'loyalty_cashback_campaign_step_7', ctx.key, {'__loop_index_1': __loop_index_1, 'active_count': active_count, 'customers': customers, 'products': products})
        ctx.call_remote_async(operator_name = 'product', function_name = 'is_available', key = p, params = (reply_to,))

@marketplace_operator.register
async def loyalty_cashback_campaign_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, active_count, customers, products) = (params.get('__loop_index_1'), params.get('active_count'), params.get('customers'), params.get('products'))
    total_points_granted = 0
    __loop_index_2 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'loyalty_cashback_campaign_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'active_count': active_count, 'customers': customers, 'total_points_granted': total_points_granted}, None, reply_to))

@marketplace_operator.register
async def loyalty_cashback_campaign_step_4(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, active_count, customers, total_points_granted) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('active_count'), params.get('customers'), params.get('total_points_granted'))
    if __loop_index_2 >= len(customers):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'loyalty_cashback_campaign_step_5', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'active_count': active_count, 'customers': customers, 'total_points_granted': total_points_granted}, None, reply_to))
    else:
        c = customers[__loop_index_2]
        __loop_index_2 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'loyalty_cashback_campaign_step_6', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'active_count': active_count, 'customers': customers, 'total_points_granted': total_points_granted})
        ctx.call_remote_async(operator_name = 'customer', function_name = 'earn_loyalty_points', key = c, params = (active_count * 100, reply_to))

@marketplace_operator.register
async def loyalty_cashback_campaign_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, active_count, customers, total_points_granted) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('active_count'), params.get('customers'), params.get('total_points_granted'))
    return send_reply(ctx, reply_to, total_points_granted)

@marketplace_operator.register
async def loyalty_cashback_campaign_step_6(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, active_count, customers, total_points_granted) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('active_count'), params.get('customers'), params.get('total_points_granted'))
    total_points_granted += active_count
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'loyalty_cashback_campaign_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, 'active_count': active_count, 'customers': customers, 'total_points_granted': total_points_granted}, None, reply_to))

@marketplace_operator.register
async def loyalty_cashback_campaign_step_7(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, active_count, customers, products) = (params.get('__loop_index_1'), params.get('active_count'), params.get('customers'), params.get('products'))
    if attr_1:
        active_count += 1
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'loyalty_cashback_campaign_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'active_count': active_count, 'customers': customers, 'products': products}, None, reply_to))


@marketplace_operator.register
async def get_seller_product_prices(
    ctx: StatefulFunction, seller: str, products: list[str], 
reply_to: list = None) -> list[int]:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_seller_product_prices_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products, 'seller': seller}, None, reply_to))

@marketplace_operator.register
async def get_seller_product_prices_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products, seller) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'), params.get('seller'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_seller_product_prices_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products, 'seller': seller}, None, reply_to))
    else:
        p = products[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'get_seller_product_prices_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'p': p, 'products': products, 'seller': seller})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_seller', key = p, params = (reply_to,))

@marketplace_operator.register
async def get_seller_product_prices_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products, seller) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'), params.get('seller'))
    return send_reply(ctx, reply_to, _comp_result_1)

@marketplace_operator.register
async def get_seller_product_prices_step_4(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, p, products, seller) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('p'), params.get('products'), params.get('seller'))
    if attr_3 == seller:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'get_seller_product_prices_step_5', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products, 'seller': seller})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_price', key = p, params = (reply_to,))
    else:
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_seller_product_prices_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products, 'seller': seller}, None, reply_to))

@marketplace_operator.register
async def get_seller_product_prices_step_5(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products, seller) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'), params.get('seller'))
    _comp_result_1.append(attr_1)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_seller_product_prices_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products, 'seller': seller}, None, reply_to))


@marketplace_operator.register
async def cross_entity_stats(
    ctx: StatefulFunction, sellers: list[str],
    customers: list[str],
    products: list[str],
    warehouses: list[str],
reply_to: list = None) -> str:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'customers': customers, 'products': products, 'sellers': sellers, 'warehouses': warehouses}, None, reply_to))

@marketplace_operator.register
async def cross_entity_stats_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, customers, products, sellers, warehouses) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('customers'), params.get('products'), params.get('sellers'), params.get('warehouses'))
    if __loop_index_1 >= len(sellers):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'customers': customers, 'products': products, 'sellers': sellers, 'warehouses': warehouses}, None, reply_to))
    else:
        s = sellers[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'cross_entity_stats_step_16', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'customers': customers, 'products': products, 'sellers': sellers, 'warehouses': warehouses})
        ctx.call_remote_async(operator_name = 'seller', function_name = 'get_balance', key = s, params = (reply_to,))

@marketplace_operator.register
async def cross_entity_stats_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, customers, products, sellers, warehouses) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('customers'), params.get('products'), params.get('sellers'), params.get('warehouses'))
    total_seller_balance = sum(_comp_result_1)
    _comp_result_2 = []
    __loop_index_2 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'customers': customers, 'products': products, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses}, None, reply_to))

@marketplace_operator.register
async def cross_entity_stats_step_4(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, _comp_result_1, _comp_result_2, customers, products, total_seller_balance, warehouses) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('customers'), params.get('products'), params.get('total_seller_balance'), params.get('warehouses'))
    if __loop_index_2 >= len(customers):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_5', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'customers': customers, 'products': products, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses}, None, reply_to))
    else:
        c = customers[__loop_index_2]
        __loop_index_2 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'cross_entity_stats_step_15', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'customers': customers, 'products': products, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses})
        ctx.call_remote_async(operator_name = 'customer', function_name = 'get_balance', key = c, params = (reply_to,))

@marketplace_operator.register
async def cross_entity_stats_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, _comp_result_1, _comp_result_2, customers, products, total_seller_balance, warehouses) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('customers'), params.get('products'), params.get('total_seller_balance'), params.get('warehouses'))
    total_customer_balance = sum(_comp_result_2)
    _comp_result_3 = []
    __loop_index_3 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_6', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, 'products': products, 'total_customer_balance': total_customer_balance, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses}, None, reply_to))

@marketplace_operator.register
async def cross_entity_stats_step_6(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, __loop_index_3, _comp_result_1, _comp_result_2, _comp_result_3, products, total_customer_balance, total_seller_balance, warehouses) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('__loop_index_3'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('_comp_result_3'), params.get('products'), params.get('total_customer_balance'), params.get('total_seller_balance'), params.get('warehouses'))
    if __loop_index_3 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_7', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, 'products': products, 'total_customer_balance': total_customer_balance, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses}, None, reply_to))
    else:
        p = products[__loop_index_3]
        __loop_index_3 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'cross_entity_stats_step_14', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, 'p': p, 'products': products, 'total_customer_balance': total_customer_balance, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses})
        ctx.call_remote_async(operator_name = 'product', function_name = 'is_available', key = p, params = (reply_to,))

@marketplace_operator.register
async def cross_entity_stats_step_7(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, __loop_index_3, _comp_result_1, _comp_result_2, _comp_result_3, products, total_customer_balance, total_seller_balance, warehouses) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('__loop_index_3'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('_comp_result_3'), params.get('products'), params.get('total_customer_balance'), params.get('total_seller_balance'), params.get('warehouses'))
    active_products = _comp_result_3
    _comp_result_4 = []
    __loop_index_4 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_8', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '__loop_index_4': __loop_index_4, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, '_comp_result_4': _comp_result_4, 'active_products': active_products, 'total_customer_balance': total_customer_balance, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses}, None, reply_to))

@marketplace_operator.register
async def cross_entity_stats_step_8(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, __loop_index_3, __loop_index_4, _comp_result_1, _comp_result_2, _comp_result_3, _comp_result_4, active_products, total_customer_balance, total_seller_balance, warehouses) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('__loop_index_3'), params.get('__loop_index_4'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('_comp_result_3'), params.get('_comp_result_4'), params.get('active_products'), params.get('total_customer_balance'), params.get('total_seller_balance'), params.get('warehouses'))
    if __loop_index_4 >= len(active_products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_9', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '__loop_index_4': __loop_index_4, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, '_comp_result_4': _comp_result_4, 'active_products': active_products, 'total_customer_balance': total_customer_balance, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses}, None, reply_to))
    else:
        p = active_products[__loop_index_4]
        __loop_index_4 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'cross_entity_stats_step_13', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '__loop_index_4': __loop_index_4, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, '_comp_result_4': _comp_result_4, 'active_products': active_products, 'total_customer_balance': total_customer_balance, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_stock', key = p, params = (reply_to,))

@marketplace_operator.register
async def cross_entity_stats_step_9(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, __loop_index_3, __loop_index_4, _comp_result_1, _comp_result_2, _comp_result_3, _comp_result_4, active_products, total_customer_balance, total_seller_balance, warehouses) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('__loop_index_3'), params.get('__loop_index_4'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('_comp_result_3'), params.get('_comp_result_4'), params.get('active_products'), params.get('total_customer_balance'), params.get('total_seller_balance'), params.get('warehouses'))
    avg_stock = sum(_comp_result_4)
    _comp_result_5 = []
    __loop_index_5 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_10', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '__loop_index_4': __loop_index_4, '__loop_index_5': __loop_index_5, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, '_comp_result_4': _comp_result_4, '_comp_result_5': _comp_result_5, 'active_products': active_products, 'avg_stock': avg_stock, 'total_customer_balance': total_customer_balance, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses}, None, reply_to))

@marketplace_operator.register
async def cross_entity_stats_step_10(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, __loop_index_3, __loop_index_4, __loop_index_5, _comp_result_1, _comp_result_2, _comp_result_3, _comp_result_4, _comp_result_5, active_products, avg_stock, total_customer_balance, total_seller_balance, warehouses) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('__loop_index_3'), params.get('__loop_index_4'), params.get('__loop_index_5'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('_comp_result_3'), params.get('_comp_result_4'), params.get('_comp_result_5'), params.get('active_products'), params.get('avg_stock'), params.get('total_customer_balance'), params.get('total_seller_balance'), params.get('warehouses'))
    if __loop_index_5 >= len(warehouses):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_11', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '__loop_index_4': __loop_index_4, '__loop_index_5': __loop_index_5, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, '_comp_result_4': _comp_result_4, '_comp_result_5': _comp_result_5, 'active_products': active_products, 'avg_stock': avg_stock, 'total_customer_balance': total_customer_balance, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses}, None, reply_to))
    else:
        w = warehouses[__loop_index_5]
        __loop_index_5 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'cross_entity_stats_step_12', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '__loop_index_4': __loop_index_4, '__loop_index_5': __loop_index_5, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, '_comp_result_4': _comp_result_4, '_comp_result_5': _comp_result_5, 'active_products': active_products, 'avg_stock': avg_stock, 'total_customer_balance': total_customer_balance, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses})
        ctx.call_remote_async(operator_name = 'warehouse', function_name = 'calculate_fill_rate', key = w, params = (reply_to,))

@marketplace_operator.register
async def cross_entity_stats_step_11(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, __loop_index_3, __loop_index_4, __loop_index_5, _comp_result_1, _comp_result_2, _comp_result_3, _comp_result_4, _comp_result_5, active_products, avg_stock, total_customer_balance, total_seller_balance, warehouses) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('__loop_index_3'), params.get('__loop_index_4'), params.get('__loop_index_5'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('_comp_result_3'), params.get('_comp_result_4'), params.get('_comp_result_5'), params.get('active_products'), params.get('avg_stock'), params.get('total_customer_balance'), params.get('total_seller_balance'), params.get('warehouses'))
    warehouse_fill_rates = _comp_result_5
    avg_fill = sum(warehouse_fill_rates) // len(warehouse_fill_rates) if warehouse_fill_rates else 0
    return send_reply(ctx, reply_to, (
        "Sellers balance: " + str(total_seller_balance)
        + " | Customers balance: " + str(total_customer_balance)
        + " | Active products: " + str(len(active_products))
        + " | Total stock: " + str(avg_stock)
        + " | Avg warehouse fill: " + str(avg_fill) + "%"
    ))

@marketplace_operator.register
async def cross_entity_stats_step_12(ctx: StatefulFunction, func_context, attr_9 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, __loop_index_3, __loop_index_4, __loop_index_5, _comp_result_1, _comp_result_2, _comp_result_3, _comp_result_4, _comp_result_5, active_products, avg_stock, total_customer_balance, total_seller_balance, warehouses) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('__loop_index_3'), params.get('__loop_index_4'), params.get('__loop_index_5'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('_comp_result_3'), params.get('_comp_result_4'), params.get('_comp_result_5'), params.get('active_products'), params.get('avg_stock'), params.get('total_customer_balance'), params.get('total_seller_balance'), params.get('warehouses'))
    _comp_result_5.append(attr_9)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_10', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '__loop_index_4': __loop_index_4, '__loop_index_5': __loop_index_5, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, '_comp_result_4': _comp_result_4, '_comp_result_5': _comp_result_5, 'active_products': active_products, 'avg_stock': avg_stock, 'total_customer_balance': total_customer_balance, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses}, None, reply_to))

@marketplace_operator.register
async def cross_entity_stats_step_13(ctx: StatefulFunction, func_context, attr_7 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, __loop_index_3, __loop_index_4, _comp_result_1, _comp_result_2, _comp_result_3, _comp_result_4, active_products, total_customer_balance, total_seller_balance, warehouses) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('__loop_index_3'), params.get('__loop_index_4'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('_comp_result_3'), params.get('_comp_result_4'), params.get('active_products'), params.get('total_customer_balance'), params.get('total_seller_balance'), params.get('warehouses'))
    _comp_result_4.append(attr_7)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_8', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '__loop_index_4': __loop_index_4, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, '_comp_result_4': _comp_result_4, 'active_products': active_products, 'total_customer_balance': total_customer_balance, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses}, None, reply_to))

@marketplace_operator.register
async def cross_entity_stats_step_14(ctx: StatefulFunction, func_context, attr_6 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, __loop_index_3, _comp_result_1, _comp_result_2, _comp_result_3, p, products, total_customer_balance, total_seller_balance, warehouses) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('__loop_index_3'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('_comp_result_3'), params.get('p'), params.get('products'), params.get('total_customer_balance'), params.get('total_seller_balance'), params.get('warehouses'))
    if attr_6:
        _comp_result_3.append(p)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_6', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '__loop_index_3': __loop_index_3, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, '_comp_result_3': _comp_result_3, 'products': products, 'total_customer_balance': total_customer_balance, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses}, None, reply_to))

@marketplace_operator.register
async def cross_entity_stats_step_15(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, _comp_result_1, _comp_result_2, customers, products, total_seller_balance, warehouses) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('customers'), params.get('products'), params.get('total_seller_balance'), params.get('warehouses'))
    _comp_result_2.append(attr_3)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'customers': customers, 'products': products, 'total_seller_balance': total_seller_balance, 'warehouses': warehouses}, None, reply_to))

@marketplace_operator.register
async def cross_entity_stats_step_16(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, customers, products, sellers, warehouses) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('customers'), params.get('products'), params.get('sellers'), params.get('warehouses'))
    _comp_result_1.append(attr_1)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'cross_entity_stats_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'customers': customers, 'products': products, 'sellers': sellers, 'warehouses': warehouses}, None, reply_to))


@marketplace_operator.register
async def fire_restock_notifications(
    ctx: StatefulFunction, products: list[str], threshold: int, 
reply_to: list = None) -> None:
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'fire_restock_notifications_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'threshold': threshold}, None, reply_to))

@marketplace_operator.register
async def fire_restock_notifications_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, attr_1, products, threshold) = (params.get('__loop_index_1'), params.get('attr_1'), params.get('products'), params.get('threshold'))
    if __loop_index_1 >= len(products):
        return send_reply(ctx, reply_to, None)
    else:
        p = products[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'fire_restock_notifications_step_3', ctx.key, {'__loop_index_1': __loop_index_1, 'p': p, 'products': products, 'threshold': threshold})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_stock', key = p, params = (reply_to,))

@marketplace_operator.register
async def fire_restock_notifications_step_3(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, p, products, threshold) = (params.get('__loop_index_1'), params.get('p'), params.get('products'), params.get('threshold'))
    if attr_1 < threshold:
        ctx.call_remote_async(operator_name = 'product', function_name = 'add_tag', key = p, params = ("low_stock", [{'sink': True}]))
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'fire_restock_notifications_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'attr_1': attr_1, 'products': products, 'threshold': threshold}, None, reply_to))


@marketplace_operator.register
async def checkout_with_loyalty_and_coupon(
    ctx: StatefulFunction, customer: str,
    products: list[str],
    quantities: list[int],
    coupon: str,
    warehouse: str,
reply_to: list = None) -> str:
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'checkout_with_loyalty_and_coupon_step_2', ctx.key, {'coupon': coupon, 'customer': customer, 'products': products, 'quantities': quantities, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'compute_cart_total', key = ctx.key, params = (customer, products, quantities, reply_to))

@marketplace_operator.register
async def checkout_with_loyalty_and_coupon_step_2(ctx: StatefulFunction, func_context, subtotal = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (coupon, customer, products, quantities, warehouse) = (params.get('coupon'), params.get('customer'), params.get('products'), params.get('quantities'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'checkout_with_loyalty_and_coupon_step_3', ctx.key, {'customer': customer, 'products': products, 'quantities': quantities, 'subtotal': subtotal, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'coupon', function_name = 'apply', key = coupon, params = (subtotal, reply_to))

@marketplace_operator.register
async def checkout_with_loyalty_and_coupon_step_3(ctx: StatefulFunction, func_context, discount = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, products, quantities, subtotal, warehouse) = (params.get('customer'), params.get('products'), params.get('quantities'), params.get('subtotal'), params.get('warehouse'))
    after_coupon = subtotal - discount
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'checkout_with_loyalty_and_coupon_step_4', ctx.key, {'after_coupon': after_coupon, 'customer': customer, 'products': products, 'quantities': quantities, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'get_loyalty_points', key = customer, params = (reply_to,))

@marketplace_operator.register
async def checkout_with_loyalty_and_coupon_step_4(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (after_coupon, customer, products, quantities, warehouse) = (params.get('after_coupon'), params.get('customer'), params.get('products'), params.get('quantities'), params.get('warehouse'))
    points_to_redeem = attr_3 // 2
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'checkout_with_loyalty_and_coupon_step_5', ctx.key, {'after_coupon': after_coupon, 'customer': customer, 'products': products, 'quantities': quantities, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'redeem_loyalty_points', key = customer, params = (points_to_redeem, reply_to))

@marketplace_operator.register
async def checkout_with_loyalty_and_coupon_step_5(ctx: StatefulFunction, func_context, cashback = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (after_coupon, customer, products, quantities, warehouse) = (params.get('after_coupon'), params.get('customer'), params.get('products'), params.get('quantities'), params.get('warehouse'))
    final = after_coupon - cashback
    if final < 0:
        final = 0
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'checkout_with_loyalty_and_coupon_step_6', ctx.key, {'customer': customer, 'final': final, 'products': products, 'quantities': quantities, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'get_balance', key = customer, params = (reply_to,))

@marketplace_operator.register
async def checkout_with_loyalty_and_coupon_step_6(ctx: StatefulFunction, func_context, attr_5 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (customer, final, products, quantities, warehouse) = (params.get('customer'), params.get('final'), params.get('products'), params.get('quantities'), params.get('warehouse'))

    if attr_5 < final:
        raise InsufficientFunds("Customer cannot afford cart after discounts.")
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'checkout_with_loyalty_and_coupon_step_7', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'customer': customer, 'final': final, 'products': products, 'quantities': quantities, 'warehouse': warehouse}, None, reply_to))

@marketplace_operator.register
async def checkout_with_loyalty_and_coupon_step_7(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, customer, final, products, quantities, warehouse) = (params.get('__loop_index_1'), params.get('customer'), params.get('final'), params.get('products'), params.get('quantities'), params.get('warehouse'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'checkout_with_loyalty_and_coupon_step_8', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'customer': customer, 'final': final, 'products': products, 'quantities': quantities, 'warehouse': warehouse}, None, reply_to))
    else:
        i = __loop_index_1
        __loop_index_1 += 1
        p = products[i]
        qty = quantities[i]
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'checkout_with_loyalty_and_coupon_step_12', ctx.key, {'__loop_index_1': __loop_index_1, 'customer': customer, 'final': final, 'p': p, 'products': products, 'qty': qty, 'quantities': quantities, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'product', function_name = 'deduct_stock', key = p, params = (qty, reply_to))

@marketplace_operator.register
async def checkout_with_loyalty_and_coupon_step_8(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, customer, final, products, quantities, warehouse) = (params.get('__loop_index_1'), params.get('customer'), params.get('final'), params.get('products'), params.get('quantities'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'checkout_with_loyalty_and_coupon_step_9', ctx.key, {'__loop_index_1': __loop_index_1, 'customer': customer, 'final': final})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'deduct_funds', key = customer, params = (final, reply_to))

@marketplace_operator.register
async def checkout_with_loyalty_and_coupon_step_9(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, customer, final) = (params.get('__loop_index_1'), params.get('customer'), params.get('final'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'checkout_with_loyalty_and_coupon_step_10', ctx.key, {'__loop_index_1': __loop_index_1, 'final': final})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'earn_loyalty_points', key = customer, params = (final, reply_to))

@marketplace_operator.register
async def checkout_with_loyalty_and_coupon_step_10(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, final) = (params.get('__loop_index_1'), params.get('final'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'checkout_with_loyalty_and_coupon_step_11', ctx.key, {'__loop_index_1': __loop_index_1, 'final': final})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'record_transaction', key = ctx.key, params = (final, reply_to))

@marketplace_operator.register
async def checkout_with_loyalty_and_coupon_step_11(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, final) = (params.get('__loop_index_1'), params.get('final'))
    return send_reply(ctx, reply_to, "Checkout complete. Paid: " + str(final) + ", Loyalty earned: " + str(final // 100))

@marketplace_operator.register
async def checkout_with_loyalty_and_coupon_step_12(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, customer, final, p, products, qty, quantities, warehouse) = (params.get('__loop_index_1'), params.get('customer'), params.get('final'), params.get('p'), params.get('products'), params.get('qty'), params.get('quantities'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'checkout_with_loyalty_and_coupon_step_13', ctx.key, {'__loop_index_1': __loop_index_1, 'customer': customer, 'final': final, 'products': products, 'qty': qty, 'quantities': quantities, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = p, params = (reply_to,))

@marketplace_operator.register
async def checkout_with_loyalty_and_coupon_step_13(ctx: StatefulFunction, func_context, attr_7 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, customer, final, products, qty, quantities, warehouse) = (params.get('__loop_index_1'), params.get('customer'), params.get('final'), params.get('products'), params.get('qty'), params.get('quantities'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'checkout_with_loyalty_and_coupon_step_7', ctx.key, {'__loop_index_1': __loop_index_1, 'customer': customer, 'final': final, 'products': products, 'quantities': quantities, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'remove_product', key = warehouse, params = (attr_7, qty, reply_to))


@marketplace_operator.register
async def recursive_price_sum(ctx: StatefulFunction, products: list[str], reply_to: list = None) -> int:
    if not products:
        return send_reply(ctx, reply_to, 0)
    attr_1 = products[0]
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'recursive_price_sum_step_2', ctx.key, {'products': products})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_price', key = attr_1, params = (reply_to,))

@marketplace_operator.register
async def recursive_price_sum_step_2(ctx: StatefulFunction, func_context, head_price = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (products,) = (params.get('products'),)
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'recursive_price_sum_step_3', ctx.key, {'head_price': head_price})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'recursive_price_sum', key = ctx.key, params = (products[1:], reply_to))

@marketplace_operator.register
async def recursive_price_sum_step_3(ctx: StatefulFunction, func_context, rest_sum = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (head_price,) = (params.get('head_price'),)
    return send_reply(ctx, reply_to, head_price + rest_sum)


@marketplace_operator.register
async def tag_popular_products(
    ctx: StatefulFunction, products: list[str], score_threshold: int, 
reply_to: list = None) -> int:
    tagged = 0
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'tag_popular_products_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'score_threshold': score_threshold, 'tagged': tagged}, None, reply_to))

@marketplace_operator.register
async def tag_popular_products_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, score, score_threshold, tagged) = (params.get('__loop_index_1'), params.get('products'), params.get('score'), params.get('score_threshold'), params.get('tagged'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'tag_popular_products_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'score': score, 'score_threshold': score_threshold, 'tagged': tagged}, None, reply_to))
    else:
        p = products[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'tag_popular_products_step_4', ctx.key, {'__loop_index_1': __loop_index_1, 'p': p, 'products': products, 'score_threshold': score_threshold, 'tagged': tagged})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_popularity_score', key = p, params = (reply_to,))

@marketplace_operator.register
async def tag_popular_products_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, score, score_threshold, tagged) = (params.get('__loop_index_1'), params.get('products'), params.get('score'), params.get('score_threshold'), params.get('tagged'))
    return send_reply(ctx, reply_to, tagged)

@marketplace_operator.register
async def tag_popular_products_step_4(ctx: StatefulFunction, func_context, score = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, p, products, score_threshold, tagged) = (params.get('__loop_index_1'), params.get('p'), params.get('products'), params.get('score_threshold'), params.get('tagged'))
    if score >= score_threshold:
        ctx.call_remote_async(operator_name = 'product', function_name = 'add_tag', key = p, params = ("trending", [{'sink': True}]))
        tagged += 1
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'tag_popular_products_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'score': score, 'score_threshold': score_threshold, 'tagged': tagged}, None, reply_to))


@marketplace_operator.register
async def get_customer_cart_value(
    ctx: StatefulFunction, customer: str, products: list[str], 
reply_to: list = None) -> int:
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'get_customer_cart_value_step_2', ctx.key, {'products': products})
    ctx.call_remote_async(operator_name = 'customer', function_name = 'get_cart', key = customer, params = (reply_to,))

@marketplace_operator.register
async def get_customer_cart_value_step_2(ctx: StatefulFunction, func_context, cart = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (products,) = (params.get('products'),)
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_customer_cart_value_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'cart': cart, 'products': products}, None, reply_to))

@marketplace_operator.register
async def get_customer_cart_value_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, cart, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('cart'), params.get('products'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_customer_cart_value_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'cart': cart, 'products': products}, None, reply_to))
    else:
        p = products[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'get_customer_cart_value_step_5', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'cart': cart, 'p': p, 'products': products})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = p, params = (reply_to,))

@marketplace_operator.register
async def get_customer_cart_value_step_4(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, cart, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('cart'), params.get('products'))
    total = sum(_comp_result_1)
    return send_reply(ctx, reply_to, total)

@marketplace_operator.register
async def get_customer_cart_value_step_5(ctx: StatefulFunction, func_context, attr_4 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, cart, p, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('cart'), params.get('p'), params.get('products'))
    if attr_4 in cart:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'get_customer_cart_value_step_6', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'cart': cart, 'products': products})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_price', key = p, params = (reply_to,))
    else:
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_customer_cart_value_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'cart': cart, 'products': products}, None, reply_to))

@marketplace_operator.register
async def get_customer_cart_value_step_6(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, cart, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('cart'), params.get('products'))
    _comp_result_1.append(attr_2)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_customer_cart_value_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'cart': cart, 'products': products}, None, reply_to))


@marketplace_operator.register
async def rebalance_warehouses(
    ctx: StatefulFunction, source: str,
    destination: str,
    product_id: str,
    transfer_qty: int,
reply_to: list = None) -> str:
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'rebalance_warehouses_step_2', ctx.key, {'destination': destination, 'product_id': product_id, 'source': source, 'transfer_qty': transfer_qty})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'get_product_quantity', key = source, params = (product_id, reply_to))

@marketplace_operator.register
async def rebalance_warehouses_step_2(ctx: StatefulFunction, func_context, available_in_source = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (destination, product_id, source, transfer_qty) = (params.get('destination'), params.get('product_id'), params.get('source'), params.get('transfer_qty'))
    if available_in_source < transfer_qty:
        raise InsufficientStock("Source warehouse does not have enough stock.")
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'rebalance_warehouses_step_3', ctx.key, {'destination': destination, 'product_id': product_id, 'source': source, 'transfer_qty': transfer_qty})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'get_available_capacity', key = destination, params = (reply_to,))

@marketplace_operator.register
async def rebalance_warehouses_step_3(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (destination, product_id, source, transfer_qty) = (params.get('destination'), params.get('product_id'), params.get('source'), params.get('transfer_qty'))

    if attr_2 < transfer_qty:
        raise WarehouseCapacityExceeded("Destination warehouse cannot fit the transfer.")
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'rebalance_warehouses_step_4', ctx.key, {'destination': destination, 'product_id': product_id, 'transfer_qty': transfer_qty})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'remove_product', key = source, params = (product_id, transfer_qty, reply_to))

@marketplace_operator.register
async def rebalance_warehouses_step_4(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (destination, product_id, transfer_qty) = (params.get('destination'), params.get('product_id'), params.get('transfer_qty'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'rebalance_warehouses_step_5', ctx.key, {'product_id': product_id, 'transfer_qty': transfer_qty})
    ctx.call_remote_async(operator_name = 'warehouse', function_name = 'store_product', key = destination, params = (product_id, transfer_qty, reply_to))

@marketplace_operator.register
async def rebalance_warehouses_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (product_id, transfer_qty) = (params.get('product_id'), params.get('transfer_qty'))
    return send_reply(ctx, reply_to, "Transferred " + str(transfer_qty) + " units of " + product_id)


@marketplace_operator.register
async def full_seller_onboarding(
    ctx: StatefulFunction, seller: str,
    products: list[str],
    quantities: list[int],
    warehouse: str,
reply_to: list = None) -> str:
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'full_seller_onboarding_step_2', ctx.key, {'products': products, 'quantities': quantities, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def full_seller_onboarding_step_2(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (products, quantities, seller, warehouse) = (params.get('products'), params.get('quantities'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'full_seller_onboarding_step_3', ctx.key, {'products': products, 'quantities': quantities, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'register_seller', key = ctx.key, params = (attr_1, reply_to))

@marketplace_operator.register
async def full_seller_onboarding_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (products, quantities, seller, warehouse) = (params.get('products'), params.get('quantities'), params.get('seller'), params.get('warehouse'))
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'full_seller_onboarding_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'seller': seller, 'warehouse': warehouse}, None, reply_to))

@marketplace_operator.register
async def full_seller_onboarding_step_4(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, quantities, seller, warehouse) = (params.get('__loop_index_1'), params.get('products'), params.get('quantities'), params.get('seller'), params.get('warehouse'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'full_seller_onboarding_step_5', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'seller': seller, 'warehouse': warehouse}, None, reply_to))
    else:
        p = products[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'full_seller_onboarding_step_8', ctx.key, {'__loop_index_1': __loop_index_1, 'p': p, 'products': products, 'quantities': quantities, 'seller': seller, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'seller', function_name = 'add_product', key = seller, params = (p, reply_to))

@marketplace_operator.register
async def full_seller_onboarding_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, quantities, seller, warehouse) = (params.get('__loop_index_1'), params.get('products'), params.get('quantities'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'full_seller_onboarding_step_6', ctx.key, {'__loop_index_1': __loop_index_1, 'seller': seller})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'batch_restock', key = ctx.key, params = (products, quantities, warehouse, reply_to))

@marketplace_operator.register
async def full_seller_onboarding_step_6(ctx: StatefulFunction, func_context, result = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, seller) = (params.get('__loop_index_1'), params.get('seller'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'full_seller_onboarding_step_7', ctx.key, {'__loop_index_1': __loop_index_1, 'result': result})
    ctx.call_remote_async(operator_name = 'seller', function_name = 'get_seller_id', key = seller, params = (reply_to,))

@marketplace_operator.register
async def full_seller_onboarding_step_7(ctx: StatefulFunction, func_context, attr_7 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, result) = (params.get('__loop_index_1'), params.get('result'))
    return send_reply(ctx, reply_to, "Onboarded seller " + attr_7 + ". " + result)

@marketplace_operator.register
async def full_seller_onboarding_step_8(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, p, products, quantities, seller, warehouse) = (params.get('__loop_index_1'), params.get('p'), params.get('products'), params.get('quantities'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'full_seller_onboarding_step_9', ctx.key, {'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = p, params = (reply_to,))

@marketplace_operator.register
async def full_seller_onboarding_step_9(ctx: StatefulFunction, func_context, attr_4 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, products, quantities, seller, warehouse) = (params.get('__loop_index_1'), params.get('products'), params.get('quantities'), params.get('seller'), params.get('warehouse'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'full_seller_onboarding_step_4', ctx.key, {'__loop_index_1': __loop_index_1, 'products': products, 'quantities': quantities, 'seller': seller, 'warehouse': warehouse})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'list_product', key = ctx.key, params = (attr_4, reply_to))


@marketplace_operator.register
async def get_product_dict(ctx: StatefulFunction, products: list[str], reply_to: list = None) -> dict[str, int]:
    _comp_result_1 = {}
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_product_dict_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products}, None, reply_to))

@marketplace_operator.register
async def get_product_dict_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_product_dict_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products}, None, reply_to))
    else:
        p = products[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'get_product_dict_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'p': p, 'products': products})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_product_id', key = p, params = (reply_to,))

@marketplace_operator.register
async def get_product_dict_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'))
    return send_reply(ctx, reply_to, _comp_result_1)

@marketplace_operator.register
async def get_product_dict_step_4(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, p, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('p'), params.get('products'))
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'get_product_dict_step_5', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'attr_1': attr_1, 'products': products})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_price', key = p, params = (reply_to,))

@marketplace_operator.register
async def get_product_dict_step_5(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, attr_1, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('attr_1'), params.get('products'))
    _comp_result_1[attr_1] = attr_2
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_product_dict_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products}, None, reply_to))


@marketplace_operator.register
async def count_high_rated_products(
    ctx: StatefulFunction, products: list[str], min_rating: int, 
reply_to: list = None) -> int:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'count_high_rated_products_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'min_rating': min_rating, 'products': products}, None, reply_to))

@marketplace_operator.register
async def count_high_rated_products_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, min_rating, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('min_rating'), params.get('products'))
    if __loop_index_1 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'count_high_rated_products_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'min_rating': min_rating, 'products': products}, None, reply_to))
    else:
        p = products[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'count_high_rated_products_step_4', ctx.key, {'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'min_rating': min_rating, 'p': p, 'products': products})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_average_rating', key = p, params = (reply_to,))

@marketplace_operator.register
async def count_high_rated_products_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, min_rating, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('min_rating'), params.get('products'))
    high_rated = _comp_result_1
    return send_reply(ctx, reply_to, len(high_rated))

@marketplace_operator.register
async def count_high_rated_products_step_4(ctx: StatefulFunction, func_context, attr_2 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, min_rating, p, products) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('min_rating'), params.get('p'), params.get('products'))
    if attr_2 >= min_rating:
        _comp_result_1.append(p)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'count_high_rated_products_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'min_rating': min_rating, 'products': products}, None, reply_to))


@marketplace_operator.register
async def get_ret_tuple(ctx: StatefulFunction, product: str, reply_to: list = None) -> tuple[int, int]:
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'get_ret_tuple_step_2', ctx.key, {'product': product})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_price', key = product, params = (reply_to,))

@marketplace_operator.register
async def get_ret_tuple_step_2(ctx: StatefulFunction, func_context, price = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (product,) = (params.get('product'),)
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'get_ret_tuple_step_3', ctx.key, {'price': price})
    ctx.call_remote_async(operator_name = 'product', function_name = 'get_stock', key = product, params = (reply_to,))

@marketplace_operator.register
async def get_ret_tuple_step_3(ctx: StatefulFunction, func_context, stock = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (price,) = (params.get('price'),)
    return send_reply(ctx, reply_to, (price, stock))


@marketplace_operator.register
async def unpack_and_use_tuple(ctx: StatefulFunction, product: str, reply_to: list = None) -> str:
    reply_to = push_continuation(ctx, reply_to, 'marketplace', 'unpack_and_use_tuple_step_2', ctx.key, {})
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'get_ret_tuple', key = ctx.key, params = (product, reply_to))

@marketplace_operator.register
async def unpack_and_use_tuple_step_2(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    price, stock = attr_1
    return send_reply(ctx, reply_to, "Price: " + str(price) + ", Stock: " + str(stock))


@marketplace_operator.register
async def nested_comprehension_test(
    ctx: StatefulFunction, sellers: list[str], products: list[str], 
reply_to: list = None) -> list[int]:
    _comp_result_1 = []
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'nested_comprehension_test_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products, 'sellers': sellers}, None, reply_to))

@marketplace_operator.register
async def nested_comprehension_test_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products, sellers) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'), params.get('sellers'))
    if __loop_index_1 >= len(sellers):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'nested_comprehension_test_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '_comp_result_1': _comp_result_1, 'products': products, 'sellers': sellers}, None, reply_to))
    else:
        s = sellers[__loop_index_1]
        __loop_index_1 += 1
        _comp_result_2 = []
        __loop_index_2 = 0
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'nested_comprehension_test_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'products': products, 's': s, 'sellers': sellers}, None, reply_to))

@marketplace_operator.register
async def nested_comprehension_test_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, _comp_result_1, products, sellers) = (params.get('__loop_index_1'), params.get('_comp_result_1'), params.get('products'), params.get('sellers'))
    return send_reply(ctx, reply_to, _comp_result_1)

@marketplace_operator.register
async def nested_comprehension_test_step_4(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, _comp_result_1, _comp_result_2, products, s, sellers) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('products'), params.get('s'), params.get('sellers'))
    if __loop_index_2 >= len(products):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'nested_comprehension_test_step_5', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'products': products, 's': s, 'sellers': sellers}, None, reply_to))
    else:
        p = products[__loop_index_2]
        __loop_index_2 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'nested_comprehension_test_step_6', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'p': p, 'products': products, 's': s, 'sellers': sellers})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_seller', key = p, params = (reply_to,))

@marketplace_operator.register
async def nested_comprehension_test_step_5(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, _comp_result_1, _comp_result_2, products, s, sellers) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('products'), params.get('s'), params.get('sellers'))
    _comp_result_1.append(sum(_comp_result_2))
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'nested_comprehension_test_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'products': products, 'sellers': sellers}, None, reply_to))

@marketplace_operator.register
async def nested_comprehension_test_step_6(ctx: StatefulFunction, func_context, attr_3 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, _comp_result_1, _comp_result_2, p, products, s, sellers) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('p'), params.get('products'), params.get('s'), params.get('sellers'))
    if attr_3 == s:
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'nested_comprehension_test_step_7', ctx.key, {'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'products': products, 's': s, 'sellers': sellers})
        ctx.call_remote_async(operator_name = 'product', function_name = 'get_price', key = p, params = (reply_to,))
    else:
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'nested_comprehension_test_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'products': products, 's': s, 'sellers': sellers}, None, reply_to))

@marketplace_operator.register
async def nested_comprehension_test_step_7(ctx: StatefulFunction, func_context, attr_1 = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, __loop_index_2, _comp_result_1, _comp_result_2, products, s, sellers) = (params.get('__loop_index_1'), params.get('__loop_index_2'), params.get('_comp_result_1'), params.get('_comp_result_2'), params.get('products'), params.get('s'), params.get('sellers'))
    _comp_result_2.append(attr_1)
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'nested_comprehension_test_step_4', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, '__loop_index_2': __loop_index_2, '_comp_result_1': _comp_result_1, '_comp_result_2': _comp_result_2, 'products': products, 's': s, 'sellers': sellers}, None, reply_to))


@marketplace_operator.register
async def dispatch_all_pending(ctx: StatefulFunction, warehouse: str, order_ids: list[str], reply_to: list = None) -> int:
    dispatched = 0
    __loop_index_1 = 0
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'dispatch_all_pending_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'dispatched': dispatched, 'order_ids': order_ids, 'warehouse': warehouse}, None, reply_to))

@marketplace_operator.register
async def dispatch_all_pending_step_2(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, dispatched, order_ids, warehouse) = (params.get('__loop_index_1'), params.get('dispatched'), params.get('order_ids'), params.get('warehouse'))
    if __loop_index_1 >= len(order_ids):
        ctx.call_remote_async(operator_name = 'marketplace', function_name = 'dispatch_all_pending_step_3', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'dispatched': dispatched, 'order_ids': order_ids, 'warehouse': warehouse}, None, reply_to))
    else:
        order_id = order_ids[__loop_index_1]
        __loop_index_1 += 1
        reply_to = push_continuation(ctx, reply_to, 'marketplace', 'dispatch_all_pending_step_4', ctx.key, {'__loop_index_1': __loop_index_1, 'dispatched': dispatched, 'order_ids': order_ids, 'warehouse': warehouse})
        ctx.call_remote_async(operator_name = 'warehouse', function_name = 'dispatch_shipment', key = warehouse, params = (order_id, reply_to))

@marketplace_operator.register
async def dispatch_all_pending_step_3(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, dispatched, order_ids, warehouse) = (params.get('__loop_index_1'), params.get('dispatched'), params.get('order_ids'), params.get('warehouse'))
    return send_reply(ctx, reply_to, dispatched)

@marketplace_operator.register
async def dispatch_all_pending_step_4(ctx: StatefulFunction, func_context, placeholder_return = None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, dispatched, order_ids, warehouse) = (params.get('__loop_index_1'), params.get('dispatched'), params.get('order_ids'), params.get('warehouse'))
    dispatched += 1
    ctx.call_remote_async(operator_name = 'marketplace', function_name = 'dispatch_all_pending_step_2', key = ctx.key, params = ({'__loop_index_1': __loop_index_1, 'dispatched': dispatched, 'order_ids': order_ids, 'warehouse': warehouse}, None, reply_to))

