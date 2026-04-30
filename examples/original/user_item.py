from styx_compiler.api import entity, send_async, gather
from typing import Optional
import logging
from typing import TypeVar, Type, Callable, Any



class NotEnoughBalance(Exception):
    pass


class OutOfStock(Exception):
    pass


@entity
class Coupon:
    def __init__(self, code: str, discount: int):
        self.code: str = code
        self.discount: int = discount

    def __key__(self) -> str:
        return self.code

    def get_discount(self) -> int:
        return self.discount


@entity
class Item:
    def __init__(self, item_name: str, price: int):
        self.item_name: str = item_name
        self.stock: int = 0
        self.price: int = price

    def __key__(self) -> str:
        return self.item_name

    def get_price(self) -> int:
        return self.price

    def get_stock(self) -> int:
        return self.stock

    def update_stock(self, amount: int) -> bool:
        if (self.stock + amount) < 0:
            raise OutOfStock("Not enough stock to update.")
        self.stock += amount
        return True


@entity
class User:
    def __init__(self, username: str):
        self.username: str = username
        self.balance: int = 0
        self.myitems: list[Item] = []

    def __key__(self) -> str:
        return self.username

    def get_balance(self) -> int:
        return self.balance

    def get_items(self) -> list[Item]:
        return self.myitems

    def add_balance(self, amount: int) -> bool:
        self.balance += amount
        return True


    def simple_loop(self, items: list[Item]) -> int:
        total = 0
        for item in items:
            total += item.get_price()

        print(total)
        return total

    def buy_item(self, amount: int, item: Item) -> bool:
        total_price = amount * item.get_price()

        if self.balance < total_price:
            raise NotEnoughBalance("Not enough balance to buy the item.")

        item.update_stock(-amount)

        self.balance -= total_price
        self.myitems.append(item)
        return True

    def drain_stock(self, item: Item) -> int:
        total = 0

        while 0 < (item.get_stock() - 1):
            item.update_stock(-1)
            total += 1
        return total


    def bulk_purchase_with_tiers(self, cart: list[Item], quantities: list[int]) -> str:
        total_cost = 0

        for index in range(len(cart)):
            item = cart[index]
            requested_amount = quantities[index]

            if item.get_stock() >= requested_amount:
                current_item_cost = 0

                for unit in range(1, requested_amount + 1):
                    if unit > 50:
                        current_item_cost += int(item.get_price() * 0.8)
                    elif unit > 10:
                        current_item_cost += int(item.get_price() * 0.9)
                    else:
                        current_item_cost += item.get_price()

                if (total_cost + current_item_cost) > self.balance:
                    raise NotEnoughBalance("Cannot afford the entire cart.")

                item.update_stock(-requested_amount)
                total_cost += current_item_cost

                for _ in range(requested_amount):
                    self.myitems.append(item)
            else:
                logging.warning(f"Skipping {item} due to low stock.")

        self.balance -= total_cost
        return "Bulk purchase complete. Remaining balance: " + str(self.balance)

    def inventory_value(self) -> int:
        return sum(item.get_price() for item in self.myitems if item.get_price() > 20)

    def my_item_prices(self) -> list[int]:
        return [item.get_price() for item in self.myitems]

    def ret_tuple(self, item: Item) -> tuple[int, int]:
        return (item.get_price(), item.get_stock())

    def ret_dict(self, item: Item) -> dict[str, int]:
        price, stock = self.ret_tuple(item)
        return {"price": price, "stock": stock}

    def fire_and_forget(self, item: Item) -> None:
        send_async(item.update_stock(1))

    def demo(self) -> str:
        for item in self.myitems:
            for _ in range(100):
                send_async(self.helper(item))
        return "demo complete"

    def helper(self, item: Item) -> int:
        item.update_stock(1)
        return 1

    def demo2(self, item: Optional[Item] = None) -> str:
        if item is None:
            return "No item provided"
        item.update_stock(1)
        return "demo complete"

    def recursion_test(self, items: list[Item]) -> int:
        if not items:
            return 0
        return items[0].get_price() + self.recursion_test(items[1:])

    def comprehensions(self, items: list[Item]) -> dict[Item, int]:
        return {item: item.get_stock() for item in items}

    def type_test(self, hard: list[list[dict[Item, int]]], easy: list[list[Item]]) -> str:
        temp = easy[0][0]
        temp.get_stock()

        list(hard[0][0].keys())[0].get_stock()

        temp4 = self.myitems[0]
        temp4.get_stock()

        stock_val = self.myitems[0].get_stock()

        lst = [self.myitems[0], self.myitems[1]]
        stock = lst[0].get_stock()

        return "hello"

    def process_cart_with_limits(self, cart: list[Item], max_spend: int) -> dict:
        purchased = {}
        total_spent = 0

        for item in cart:
            price = item.get_price()

            if price > self.balance:
                continue  # can't afford even one, skip

            if total_spent >= max_spend:
                break  # hit the spending cap, stop processing cart

            units_bought = 0
            while 0 < item.get_stock():
                if total_spent + price > max_spend:
                    break  # inner break: this item would exceed cap
                if price > self.balance:
                    break  # inner break: ran out of personal balance mid-item
                item.update_stock(-1)
                self.balance -= price
                total_spent += price
                units_bought += 1

            if units_bought > 0:
                purchased[item] = units_bought

        return purchased

    def transfer_balance(self, recipient: 'User', amount: int) -> bool:
        if self.balance < amount:
            raise NotEnoughBalance("Insufficient balance for transfer.")
        self.balance -= amount
        recipient.add_balance(amount)
        return True

    def multi_restock(self, items: list[Item], amounts: list[int]) -> int:
        total_added = 0
        for item, amount in zip(items, amounts):
            item.update_stock(amount)
            total_added += amount
        return total_added

    def most_valuable_item_price(self) -> int:
        if not self.myitems:
            return 0
        return max(item.get_price() for item in self.myitems)

    def can_afford_cart(self, items: list[Item]) -> bool:
        total = sum(item.get_price() for item in items)
        return self.balance >= total

    def group_items_by_price_bucket(self, items: list[Item]) -> dict:
        return {
            'cheap': [item.get_price() for item in items if item.get_price() < 20],
            'mid': [item.get_price() for item in items if 20 <= item.get_price() <= 100],
            'expensive': [item.get_price() for item in items if item.get_price() > 100],
        }

    def is_in_stock(self, item: Item) -> bool:
        return item is not None and item.get_stock() > 0



    def get_discounted_price(self, item: Item, coupon: Coupon) -> int:
        price, discount = gather(item.get_price(), coupon.get_discount())

        discounted_price = price - discount
        return max(discounted_price, 0)

    def buy_with_coupon(self, item: Item, coupon: Optional[Coupon]) -> bool:
        if coupon is None:
            return self.buy_item(1, item)

        discounted_price = self.get_discounted_price(item, coupon)

        if self.balance < discounted_price:
            raise NotEnoughBalance("Not enough balance to buy the item with coupon.")
        if not self.is_in_stock(item):
            raise OutOfStock("Item is out of stock.")

        item.update_stock(-1)
        self.balance -= discounted_price
        self.myitems.append(item)
        return True


    def gather_in_loop(self, items: list[Item], coupons: list[Coupon]) -> int:
        total = 0
        for item, coupon in zip(items, coupons):
            price, discount = gather(item.get_price(), coupon.get_discount())
            discounted_price = price - discount
            total += discounted_price
        return total

    def inventory_value_gather(self) -> int:

        prices = gather(*[item.get_price() for item in self.myitems])
        return sum(list(prices))


    def test(self, item: Item) -> int:

        flag = False
        item.update_stock(1)

        if 1 == 1:
            flag = True
        else:
            temp = 5

        if flag:
            return 1

        return temp
