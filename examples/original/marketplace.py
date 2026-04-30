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


# ──────────────────────────────────────────
# Entity: Product
# ──────────────────────────────────────────

@entity
class Product:
    def __init__(self, product_id: str, name: str, base_price: int, seller: 'Seller'):
        self.product_id: str = product_id
        self.name: str = name
        self.base_price: int = base_price
        self.seller: 'Seller' = seller
        self.stock: int = 0
        self.total_sold: int = 0
        self.rating_sum: int = 0
        self.rating_count: int = 0
        self.tags: list[str] = []
        self.is_active: bool = True

    def __key__(self):
        return self.product_id

    def get_product_id(self) -> str:
        return self.product_id

    def get_price(self) -> int:
        return self.base_price

    def get_stock(self) -> int:
        return self.stock

    def get_seller(self) -> 'Seller':
        return self.seller

    def is_available(self) -> bool:
        return self.is_active & (self.stock > 0)

    def get_average_rating(self) -> int:
        if self.rating_count == 0:
            return 0
        return self.rating_sum // self.rating_count

    def add_stock(self, amount: int) -> bool:
        if amount <= 0:
            raise InsufficientStock("Stock amount must be positive.")
        self.stock += amount
        return True

    def deduct_stock(self, amount: int) -> bool:
        if amount <= 0:
            raise InsufficientStock("Amount must be positive.")
        if not self.is_active:
            raise InsufficientStock("Product is no longer active.")
        if self.stock < amount:
            raise InsufficientStock("Not enough stock for product.")

        self.stock -= amount
        self.total_sold += amount
        return True


    def add_rating(self, score: int) -> int:
        if (score < 0) | (score > 10):
            raise ValueError("Rating must be between 0 and 10.")
        self.rating_sum += score
        self.rating_count += 1
        return self.get_average_rating()

    def deactivate(self) -> bool:
        self.is_active = False
        return True

    def add_tag(self, tag: str) -> bool:
        if tag not in self.tags:
            self.tags.append(tag)
        return True

    def get_tags(self) -> list[str]:
        return self.tags

    def get_total_sold(self) -> int:
        return self.total_sold

    def get_popularity_score(self) -> int:
        avg = self.get_average_rating()
        return self.total_sold * 10 + avg * 50 + self.rating_count * 5


# ──────────────────────────────────────────
# Entity: Seller
# ──────────────────────────────────────────

@entity
class Seller:
    def __init__(self, seller_id: str, name: str):
        self.seller_id: str = seller_id
        self.name: str = name
        self.balance: int = 0
        self.products: list['Product'] = []
        self.total_revenue: int = 0
        self.is_suspended: bool = False
        self.penalty_points: int = 0

    def __key__(self):
        return self.seller_id

    def get_seller_id(self) -> str:
        return self.seller_id

    def is_active(self) -> bool:
        return not self.is_suspended

    def get_balance(self) -> int:
        return self.balance

    def get_revenue(self) -> int:
        return self.total_revenue

    def add_product(self, product: 'Product') -> bool:
        if self.is_suspended:
            raise SellerSuspended("Seller is suspended and cannot add products.")
        self.products.append(product)
        return True

    def credit_sale(self, amount: int) -> bool:
        if self.is_suspended:
            raise SellerSuspended("Seller is suspended.")
        self.balance += amount
        self.total_revenue += amount
        return True

    def debit_penalty(self, amount: int) -> bool:
        self.penalty_points += 1
        self.balance -= amount
        if self.penalty_points >= 5:
            self.is_suspended = True
        return True

    def withdraw(self, amount: int) -> bool:
        if self.balance < amount:
            raise InsufficientFunds("Seller does not have enough balance to withdraw.")
        self.balance -= amount
        return True

    def get_products(self) -> list['Product']:
        return self.products

    def get_penalty_points(self) -> int:
        return self.penalty_points

    def reinstate(self) -> bool:
        self.is_suspended = False
        self.penalty_points = 0
        return True


# ──────────────────────────────────────────
# Entity: Customer
# ──────────────────────────────────────────

@entity
class Customer:
    def __init__(self, customer_id: str, username: str):
        self.customer_id: str = customer_id
        self.username: str = username
        self.balance: int = 0
        self.cart: list[str] = []
        self.order_history: list[str] = []
        self.wishlist: list[str] = []
        self.loyalty_points: int = 0
        self.reviewed_products: list[str] = []

    def __key__(self):
        return self.customer_id

    def get_order_history(self) -> list[str]:
        return self.order_history

    def get_balance(self) -> int:
        return self.balance

    def get_loyalty_points(self) -> int:
        return self.loyalty_points

    def add_funds(self, amount: int) -> bool:
        self.balance += amount
        return True

    def deduct_funds(self, amount: int) -> bool:
        if self.balance < amount:
            raise InsufficientFunds("Customer does not have enough balance.")
        self.balance -= amount
        return True

    def add_to_cart(self, product_id: str) -> bool:
        if product_id not in self.cart:
            self.cart.append(product_id)
        return True

    def remove_from_cart(self, product_id: str) -> bool:
        if product_id in self.cart:
            self.cart.remove(product_id)
        return True

    def clear_cart(self) -> bool:
        self.cart = []
        return True

    def get_cart(self) -> list[str]:
        return self.cart

    def add_to_wishlist(self, product_id: str) -> bool:
        if product_id not in self.wishlist:
            self.wishlist.append(product_id)
        return True

    def add_order(self, order_id: str) -> bool:
        self.order_history.append(order_id)
        return True

    def earn_loyalty_points(self, amount_spent: int) -> int:
        earned = amount_spent // 100
        self.loyalty_points += earned
        return earned

    def redeem_loyalty_points(self, points: int) -> int:
        if self.loyalty_points < points:
            points = self.loyalty_points
        self.loyalty_points -= points
        return points * 10

    def has_reviewed(self, product_id: str) -> bool:
        return product_id in self.reviewed_products

    def mark_reviewed(self, product_id: str) -> bool:
        if product_id in self.reviewed_products:
            raise ReviewAlreadySubmitted("Customer already reviewed this product.")
        self.reviewed_products.append(product_id)
        return True

    def get_order_count(self) -> int:
        return len(self.order_history)

    def get_wishlist(self) -> list[str]:
        return self.wishlist


# ──────────────────────────────────────────
# Entity: Coupon
# ──────────────────────────────────────────

@entity
class Coupon:
    def __init__(self, code: str, discount_percent: int, max_uses: int, min_order_value: int):
        self.code: str = code
        self.discount_percent: int = discount_percent
        self.max_uses: int = max_uses
        self.uses: int = 0
        self.min_order_value: int = min_order_value
        self.is_active: bool = True

    def __key__(self):
        return self.code

    def is_valid(self) -> bool:
        return self.is_active & (self.uses < self.max_uses)

    def get_discount_percent(self) -> int:
        return self.discount_percent

    def get_min_order_value(self) -> int:
        return self.min_order_value

    def apply(self, order_value: int) -> int:
        if not self.is_valid():
            raise InvalidCoupon("Coupon is expired or has reached max uses.")
        if order_value < self.min_order_value:
            raise InvalidCoupon("Order value too low for this coupon.")
        self.uses += 1
        discount = (order_value * self.discount_percent) // 100
        return discount

    def deactivate(self) -> bool:
        self.is_active = False
        return True

    def get_remaining_uses(self) -> int:
        return self.max_uses - self.uses


# ──────────────────────────────────────────
# Entity: Warehouse
# ──────────────────────────────────────────

@entity
class Warehouse:
    def __init__(self, warehouse_id: str, capacity: int):
        self.warehouse_id: str = warehouse_id
        self.capacity: int = capacity
        self.used_capacity: int = 0
        self.product_slots: dict[str, int] = {}
        self.pending_shipments: list[str] = []
        self.total_shipped: int = 0

    def __key__(self):
        return self.warehouse_id

    def get_available_capacity(self) -> int:
        return self.capacity - self.used_capacity

    def get_used_capacity(self) -> int:
        return self.used_capacity

    def store_product(self, product_id: str, quantity: int) -> bool:
        if quantity > self.get_available_capacity():
            raise WarehouseCapacityExceeded("Not enough space in warehouse.")
        current = self.product_slots.get(product_id, 0)
        self.product_slots[product_id] = current + quantity
        self.used_capacity += quantity
        return True

    def remove_product(self, product_id: str, quantity: int) -> bool:
        if quantity <= 0:
            raise InsufficientStock("Quantity must be positive.")

        current = self.product_slots.get(product_id, 0)
        if current < quantity:
            raise InsufficientStock("Not enough of this product in warehouse.")

        new_qty = current - quantity

        if new_qty == 0:
            del self.product_slots[product_id]
        else:
            self.product_slots[product_id] = new_qty

        self.used_capacity -= quantity
        return True

    def get_product_quantity(self, product_id: str) -> int:
        return self.product_slots.get(product_id, 0)

    def add_pending_shipment(self, order_id: str) -> bool:
        self.pending_shipments.append(order_id)
        return True

    def dispatch_shipment(self, order_id: str) -> bool:
        if order_id not in self.pending_shipments:
            raise OrderAlreadyFulfilled("Order not found in pending shipments.")
        self.pending_shipments.remove(order_id)
        self.total_shipped += 1
        return True

    def get_total_shipped(self) -> int:
        return self.total_shipped

    def get_pending_count(self) -> int:
        return len(self.pending_shipments)

    def calculate_fill_rate(self) -> int:
        if self.capacity == 0:
            return 0
        return (self.used_capacity * 100) // self.capacity


# ──────────────────────────────────────────
# Entity: Marketplace
# ──────────────────────────────────────────

@entity
class Marketplace:
    def __init__(self, marketplace_id: str):
        self.marketplace_id: str = marketplace_id
        self.registered_sellers: list[str] = []
        self.registered_customers: list[str] = []
        self.all_products: list[str] = []
        self.total_transactions: int = 0
        self.total_revenue: int = 0
        self.platform_fee_percent: int = 5

    def __key__(self):
        return self.marketplace_id

    def register_seller(self, seller_id: str) -> bool:
        if seller_id not in self.registered_sellers:
            self.registered_sellers.append(seller_id)
        return True

    def register_customer(self, customer_id: str) -> bool:
        if customer_id not in self.registered_customers:
            self.registered_customers.append(customer_id)
        return True

    def list_product(self, product_id: str) -> bool:
        if product_id not in self.all_products:
            self.all_products.append(product_id)
        return True

    def record_transaction(self, amount: int) -> bool:
        fee = (amount * self.platform_fee_percent) // 100
        self.total_revenue += fee
        self.total_transactions += 1
        return True

    def get_stats(self) -> dict[str, int]:
        return {
            "sellers": len(self.registered_sellers),
            "customers": len(self.registered_customers),
            "products": len(self.all_products),
            "transactions": self.total_transactions,
            "revenue": self.total_revenue,
        }

    def get_platform_fee(self) -> int:
        return self.platform_fee_percent

    def get_total_revenue(self) -> int:
        return self.total_revenue

    def get_product_count(self) -> int:
        return len(self.all_products)

    # ── Complex orchestration methods ──────

    def purchase(
        self,
        customer: Customer,
        product: Product,
        warehouse: Warehouse,
        quantity: int,
        coupon_code: Optional[str],
        coupon: Optional[Coupon],
        use_loyalty: bool,
    ) -> str:

        if quantity <= 0:
            raise ValueError("Quantity must be positive.")

        seller = product.get_seller()

        if not product.is_available():
            raise InsufficientStock("Product not available.")

        if not seller.is_active():
            raise SellerSuspended("Seller is suspended.")

        if warehouse.get_product_quantity(product.get_product_id()) < quantity:
            raise InsufficientStock("Warehouse does not have enough stock.")

        base_cost = product.get_price() * quantity
        discount = 0

        if coupon is not None:
            if coupon_code is not None:
                if coupon.code != coupon_code:
                    raise InvalidCoupon("Coupon code mismatch.")
                discount = coupon.apply(base_cost)

        loyalty_discount = 0
        if use_loyalty:
            max_loyalty_discount = int(base_cost * 0.3)
            redeemable_points = customer.get_loyalty_points() // 2
            potential_discount = redeemable_points * 10
            actual_discount = min(max_loyalty_discount, potential_discount)
            points_to_use = actual_discount // 10
            loyalty_discount = customer.redeem_loyalty_points(points_to_use)

        final_cost = base_cost - discount - loyalty_discount
        if final_cost < 0:
            final_cost = 0

        customer.deduct_funds(final_cost)

        product.deduct_stock(quantity)
        warehouse.remove_product(product.get_product_id(), quantity)

        platform_fee = (final_cost * self.platform_fee_percent) // 100
        seller_cut = final_cost - platform_fee
        seller.credit_sale(seller_cut)

        if final_cost > 0:
            customer.earn_loyalty_points(final_cost)

        self.record_transaction(final_cost)

        order_id = (
            seller.get_seller_id()
            + "_"
            + product.get_product_id()
            + "_"
            + str(self.total_transactions)
        )

        customer.add_order(order_id)
        warehouse.add_pending_shipment(order_id)

        return order_id


    def batch_restock(
        self,
        products: list[Product],
        quantities: list[int],
        warehouse: Warehouse,
    ) -> str:
        restocked = 0
        skipped = 0

        for i in range(len(products)):
            p = products[i]
            qty = quantities[i]
            available_space = warehouse.get_available_capacity()

            if available_space >= qty:
                p.add_stock(qty)
                warehouse.store_product(p.get_product_id(), qty)
                restocked += 1
            else:
                skipped += 1

        return "Restocked: " + str(restocked) + ", Skipped: " + str(skipped)

    def compute_cart_total(
        self,
        customer: Customer,
        products: list[Product],
        quantities: list[int],
    ) -> int:

        total = 0

        for i in range(len(products)):
            p = products[i]
            qty = quantities[i]
            price = p.get_price()

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

        return total

    def submit_review(
        self,
        customer: Customer,
        product: Product,
        score: int,
    ) -> int:

        product_id = product.get_product_id()
        has_purchased = any(product_id in order_id for order_id in customer.get_order_history())
        if not has_purchased:
            raise Exception("Customer cannot review a product they haven't purchased.")

        customer.mark_reviewed(product.get_product_id())
        new_avg = product.add_rating(score)

        send_async(customer.earn_loyalty_points(20))
        return new_avg

    def get_top_product_scores(self, products: list[Product]) -> list[int]:
        scores = [p.get_popularity_score() for p in products]
        return scores

    def get_affordable_products(
        self, products: list[Product], budget: int
    ) -> list[Product]:
        affordable = [p for p in products if (p.get_price() <= budget) & p.is_available()]
        return affordable

    def total_wishlist_value(self, customer: Customer, products: list[Product]) -> int:
        wishlist = customer.get_wishlist()
        total = sum(p.get_price() for p in products if p.get_product_id() in wishlist)
        return total

    def suspend_seller_and_deactivate_products(
        self,
        seller: Seller,
        products: list[Product],
    ) -> str:
        seller.debit_penalty(500)
        deactivated = 0
        for p in products:
            if (p.get_seller() == seller):
                p.deactivate()
                deactivated += 1
        return "Deactivated " + str(deactivated) + " products for seller " + seller.get_seller_id()

    def restock_and_report(
        self,
        seller: Seller,
        products: list[Product],
        quantities: list[int],
        warehouse: Warehouse,
    ) -> dict[str, int]:
        total_units = 0
        total_fee = 0

        for i in range(len(products)):
            p = products[i]
            qty = quantities[i]
            fee = qty * 2

            if warehouse.get_available_capacity() < qty:
                raise WarehouseCapacityExceeded("Cannot restock: warehouse is full.")

            seller.withdraw(fee)
            p.add_stock(qty)
            warehouse.store_product(p.get_product_id(), qty)
            total_units += qty
            total_fee += fee

        return {"units_restocked": total_units, "fees_charged": total_fee}

    def process_bulk_orders(
        self,
        customers: list[Customer],
        product: Product,
        quantity_each: int,
        warehouse: Warehouse,
    ) -> str:

        if quantity_each <= 0:
            raise ValueError("Quantity must be positive.")

        seller = product.get_seller()

        success_count = 0
        skip_count = 0
        unit_price = product.get_price()
        cost = unit_price * quantity_each

        for customer in customers:

            if (
                (customer.get_balance() >= cost)
                & (product.get_stock() >= quantity_each)
                & (product.is_available())
                & (seller.is_active())
                & (warehouse.get_product_quantity(product.get_product_id()) >= quantity_each)
            ):
                customer.deduct_funds(cost)
                product.deduct_stock(quantity_each)
                warehouse.remove_product(product.get_product_id(), quantity_each)

                platform_fee = (cost * self.platform_fee_percent) // 100
                seller.credit_sale(cost - platform_fee)

                customer.earn_loyalty_points(cost)
                self.record_transaction(cost)

                success_count += 1
            else:
                skip_count += 1

        return f"Bulk orders done. Success: {success_count}, Skipped: {skip_count}"

    def warehouse_health_check(self, warehouses: list[Warehouse]) -> list[int]:
        return [w.calculate_fill_rate() for w in warehouses]

    def find_overstocked_warehouses(
        self, warehouses: list[Warehouse], threshold: int
    ) -> list[Warehouse]:
        return [w for w in warehouses if w.calculate_fill_rate() > threshold]

    def seller_revenue_summary(self, sellers: list[Seller]) -> dict[Seller, int]:
        return {s: s.get_revenue() for s in sellers}

    def rank_products_by_popularity(self, products: list[Product]) -> list[int]:
        scores = [p.get_popularity_score() for p in products]
        scores.sort(reverse=True)
        return scores

    def total_platform_earnings_from_sellers(self, sellers: list[Seller]) -> int:
        total = sum(
            (s.get_revenue() * self.platform_fee_percent) // 100
            for s in sellers
        )
        return total


    def multi_product_availability_check(
        self, products: list[Product], quantities: list[int]
    ) -> bool:

        for i in range(len(products)):
            p = products[i]
            if (
                (p.get_stock() < quantities[i])
                | (not p.is_available())
                | (not p.get_seller().is_active())
            ):
                return False

        return True

    def compute_order_breakdown(
        self,
        products: list[Product],
        quantities: list[int],
        coupon: Coupon,
    ) -> tuple[int, int, int]:
        subtotal = 0
        for i in range(len(products)):
            subtotal += products[i].get_price() * quantities[i]

        discount = coupon.apply(subtotal)
        final = subtotal - discount

        return (subtotal, discount, final)

    def loyalty_cashback_campaign(
        self, customers: list[Customer], products: list[Product]
    ) -> int:
        active_count = 0
        for p in products:
            if p.is_available():
                active_count += 1

        total_points_granted = 0
        for c in customers:
            c.earn_loyalty_points(active_count * 100)
            total_points_granted += active_count

        return total_points_granted

    def get_seller_product_prices(
        self, seller: Seller, products: list[Product]
    ) -> list[int]:
        return [
            p.get_price()
            for p in products
            if p.get_seller() == seller
        ]

    def cross_entity_stats(
        self,
        sellers: list[Seller],
        customers: list[Customer],
        products: list[Product],
        warehouses: list[Warehouse],
    ) -> str:
        total_seller_balance = sum(s.get_balance() for s in sellers)
        total_customer_balance = sum(c.get_balance() for c in customers)
        active_products = [p for p in products if p.is_available()]
        avg_stock = sum(p.get_stock() for p in active_products)
        warehouse_fill_rates = [w.calculate_fill_rate() for w in warehouses]
        avg_fill = sum(warehouse_fill_rates) // len(warehouse_fill_rates) if warehouse_fill_rates else 0

        return (
            "Sellers balance: " + str(total_seller_balance)
            + " | Customers balance: " + str(total_customer_balance)
            + " | Active products: " + str(len(active_products))
            + " | Total stock: " + str(avg_stock)
            + " | Avg warehouse fill: " + str(avg_fill) + "%"
        )

    def fire_restock_notifications(
        self, products: list[Product], threshold: int
    ) -> None:
        for p in products:
            if p.get_stock() < threshold:
                send_async(p.add_tag("low_stock"))

    def checkout_with_loyalty_and_coupon(
        self,
        customer: Customer,
        products: list[Product],
        quantities: list[int],
        coupon: Coupon,
        warehouse: Warehouse,
    ) -> str:
        subtotal = self.compute_cart_total(customer, products, quantities)

        discount = coupon.apply(subtotal)
        after_coupon = subtotal - discount

        points_to_redeem = customer.get_loyalty_points() // 2
        cashback = customer.redeem_loyalty_points(points_to_redeem)
        final = after_coupon - cashback
        if final < 0:
            final = 0

        if customer.get_balance() < final:
            raise InsufficientFunds("Customer cannot afford cart after discounts.")

        for i in range(len(products)):
            p = products[i]
            qty = quantities[i]
            p.deduct_stock(qty)
            warehouse.remove_product(p.get_product_id(), qty)

        customer.deduct_funds(final)
        customer.earn_loyalty_points(final)
        self.record_transaction(final)

        return "Checkout complete. Paid: " + str(final) + ", Loyalty earned: " + str(final // 100)

    def recursive_price_sum(self, products: list[Product]) -> int:
        if not products:
            return 0
        head_price = products[0].get_price()
        rest_sum = self.recursive_price_sum(products[1:])
        return head_price + rest_sum

    def tag_popular_products(
        self, products: list[Product], score_threshold: int
    ) -> int:
        tagged = 0
        for p in products:
            score = p.get_popularity_score()
            if score >= score_threshold:
                send_async(p.add_tag("trending"))
                tagged += 1
        return tagged

    def get_customer_cart_value(
        self, customer: Customer, products: list[Product]
    ) -> int:
        cart = customer.get_cart()
        total = sum(p.get_price() for p in products if p.get_product_id() in cart)
        return total

    def rebalance_warehouses(
        self,
        source: Warehouse,
        destination: Warehouse,
        product_id: str,
        transfer_qty: int,
    ) -> str:
        available_in_source = source.get_product_quantity(product_id)
        if available_in_source < transfer_qty:
            raise InsufficientStock("Source warehouse does not have enough stock.")

        if destination.get_available_capacity() < transfer_qty:
            raise WarehouseCapacityExceeded("Destination warehouse cannot fit the transfer.")

        source.remove_product(product_id, transfer_qty)
        destination.store_product(product_id, transfer_qty)

        return "Transferred " + str(transfer_qty) + " units of " + product_id

    def full_seller_onboarding(
        self,
        seller: Seller,
        products: list[Product],
        quantities: list[int],
        warehouse: Warehouse,
    ) -> str:
        self.register_seller(seller.get_seller_id())

        for p in products:
            seller.add_product(p)
            self.list_product(p.get_product_id())

        result = self.batch_restock(products, quantities, warehouse)

        return "Onboarded seller " + seller.get_seller_id() + ". " + result

    def get_product_dict(self, products: list[Product]) -> dict[str, int]:
        return {p.get_product_id(): p.get_price() for p in products}

    def count_high_rated_products(
        self, products: list[Product], min_rating: int
    ) -> int:
        high_rated = [p for p in products if p.get_average_rating() >= min_rating]
        return len(high_rated)

    def get_ret_tuple(self, product: Product) -> tuple[int, int]:
        price = product.get_price()
        stock = product.get_stock()
        return (price, stock)

    def unpack_and_use_tuple(self, product: Product) -> str:
        price, stock = self.get_ret_tuple(product)
        return "Price: " + str(price) + ", Stock: " + str(stock)

    def nested_comprehension_test(
        self, sellers: list[Seller], products: list[Product]
    ) -> list[int]:
        return [
            sum(p.get_price() for p in products if p.get_seller() == s)
            for s in sellers
        ]

    def dispatch_all_pending(self, warehouse: Warehouse, order_ids: list[str]) -> int:
        dispatched = 0
        for order_id in order_ids:
            warehouse.dispatch_shipment(order_id)
            dispatched += 1
        return dispatched
