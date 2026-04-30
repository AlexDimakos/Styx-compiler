from __future__ import annotations
from typing import Any, Dict, Optional
import datetime
from styx_compiler.api import entity, send_async, get_entity_by_key, gather, exists


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


# ──────────────────────────────────────────
# Entity: Warehouse
# Key: w_id (int)
# ──────────────────────────────────────────

@entity
class Warehouse:
    def __init__(self, w_id: int, W_NAME: str, W_STREET_1: str, W_STREET_2: str,
                 W_CITY: str, W_STATE: str, W_ZIP: str, W_TAX: float, W_YTD: float):
        self.w_id: int = w_id
        self.W_NAME: str = W_NAME
        self.W_STREET_1: str = W_STREET_1
        self.W_STREET_2: str = W_STREET_2
        self.W_CITY: str = W_CITY
        self.W_STATE: str = W_STATE
        self.W_ZIP: str = W_ZIP
        self.W_TAX: float = W_TAX
        self.W_YTD: float = W_YTD

    def __key__(self) -> int:
        return self.w_id

    def get_warehouse(self) -> Dict:
        if not exists(self):
            raise WHDoesNotExist(f"Warehouse with key: {self} does not exist.")
        data = {
            'W_NAME': self.W_NAME, 'W_TAX': self.W_TAX, 'W_YTD': self.W_YTD,
            'W_STREET_1': self.W_STREET_1, 'W_STREET_2': self.W_STREET_2,
            'W_CITY': self.W_CITY, 'W_STATE': self.W_STATE, 'W_ZIP': self.W_ZIP,
        }
        return data

    def pay(self, h_amount: float) -> Dict:
        if not exists(self):
            raise WHDoesNotExist(f"Warehouse with key: {self} does not exist")
        self.W_YTD = float(self.W_YTD) + h_amount
        data = {
            'W_NAME': self.W_NAME, 'W_TAX': self.W_TAX, 'W_YTD': self.W_YTD,
            'W_STREET_1': self.W_STREET_1, 'W_STREET_2': self.W_STREET_2,
            'W_CITY': self.W_CITY, 'W_STATE': self.W_STATE, 'W_ZIP': self.W_ZIP,
        }
        return data


# ──────────────────────────────────────────
# Entity: District
# Key: (D_W_ID, D_ID)  →  partitioned on D_W_ID (Warehouse)
# ──────────────────────────────────────────

@entity
class District:
    def __init__(self, D_ID: int, D_W_ID: Warehouse, D_NAME: str, D_STREET_1: str, D_STREET_2: str,
                 D_CITY: str, D_STATE: str, D_ZIP: str, D_TAX: float, D_YTD: float,
                 D_NEXT_O_ID: int):
        self.D_ID: int = D_ID
        self.D_W_ID: Warehouse = D_W_ID
        self.D_NAME: str = D_NAME
        self.D_STREET_1: str = D_STREET_1
        self.D_STREET_2: str = D_STREET_2
        self.D_CITY: str = D_CITY
        self.D_STATE: str = D_STATE
        self.D_ZIP: str = D_ZIP
        self.D_TAX: float = D_TAX
        self.D_YTD: float = D_YTD
        self.D_NEXT_O_ID: int = D_NEXT_O_ID

    def __key__(self) -> tuple[Warehouse, int]:
        return (self.D_W_ID, self.D_ID)

    def get_district(self, w_id: Warehouse, d_id: int, c_id: int,
                     o_entry_d: str, i_ids: list[Item], i_qtys: list[int],
                     i_w_ids: list[Warehouse], all_local: bool) -> Dict:
        if not exists(self):
            raise DistrictDoesNotExist(f"District with key: {self} does not exist")

        data = {
            'D_ID': self.D_ID, 'D_W_ID': self.D_W_ID, 'D_NAME': self.D_NAME,
            'D_TAX': self.D_TAX, 'D_YTD': self.D_YTD, 'D_NEXT_O_ID': self.D_NEXT_O_ID,
            'D_STREET_1': self.D_STREET_1, 'D_STREET_2': self.D_STREET_2,
            'D_CITY': self.D_CITY, 'D_STATE': self.D_STATE, 'D_ZIP': self.D_ZIP,
        }

        d_next_o_id = self.D_NEXT_O_ID

        # Create the Order and NewOrder records (fire-and-forget side effects)
        send_async(Order(w_id, d_id, d_next_o_id, c_id, o_entry_d, None, len(i_ids), all_local))
        send_async(NewOrder(w_id, d_id, d_next_o_id))

        self.D_NEXT_O_ID += 1

        return data

    def pay(self, h_amount: float) -> Dict:
        if not exists(self):
            raise DistrictDoesNotExist(f"District with key: {self} does not exist")

        self.D_YTD = float(self.D_YTD) + h_amount
        data = {
            'D_ID': self.D_ID, 'D_W_ID': self.D_W_ID, 'D_NAME': self.D_NAME,
            'D_TAX': self.D_TAX, 'D_YTD': self.D_YTD, 'D_NEXT_O_ID': self.D_NEXT_O_ID,
            'D_STREET_1': self.D_STREET_1, 'D_STREET_2': self.D_STREET_2,
            'D_CITY': self.D_CITY, 'D_STATE': self.D_STATE, 'D_ZIP': self.D_ZIP,
        }
        return data


# ──────────────────────────────────────────
# Entity: Item
# Key: I_ID (int)
# ──────────────────────────────────────────

@entity
class Item:
    def __init__(self, I_ID: int, I_IM_ID: int, I_NAME: str, I_PRICE: float, I_DATA: str):
        self.I_ID: int = I_ID
        self.I_IM_ID: int = I_IM_ID
        self.I_NAME: str = I_NAME
        self.I_PRICE: float = I_PRICE
        self.I_DATA: str = I_DATA

    def __key__(self) -> int:
        return self.I_ID

    def get_item(self, index: int, w_id: Warehouse, d_id: int,
                 o_entry_d: str, i_qty: int, i_w_id: Warehouse, d_next_o_id: int) -> Dict:

        if not exists(self):
            raise TPCCException("Item number is not valid")

        i_brand_generic = self.I_DATA.find("original") != -1

        stock = get_entity_by_key(Stock, (i_w_id, self))

        stock_reply = stock.update_stock(
            index, d_next_o_id, self, w_id, d_id, i_w_id,
            o_entry_d, i_qty, self.I_NAME, self.I_PRICE, i_brand_generic
        )

        return stock_reply


# ──────────────────────────────────────────
# Entity: Customer
# Key: (C_W_ID, C_D_ID, C_ID)
# ──────────────────────────────────────────

@entity
class Customer:
    def __init__(self, C_ID: int, C_D_ID: int, C_W_ID: Warehouse, C_FIRST: str, C_MIDDLE: str,
                 C_LAST: str, C_STREET_1: str, C_STREET_2: str, C_CITY: str, C_STATE: str,
                 C_ZIP: str, C_PHONE: str, C_SINCE: str, C_CREDIT: str,
                 C_CREDIT_LIM: float, C_DISCOUNT: float, C_BALANCE: float,
                 C_YTD_PAYMENT: float, C_PAYMENT_CNT: int, C_DELIVERY_CNT: int, C_DATA: str):
        self.C_ID: int = C_ID
        self.C_D_ID: int = C_D_ID
        self.C_W_ID: Warehouse = C_W_ID
        self.C_FIRST: str = C_FIRST
        self.C_MIDDLE: str = C_MIDDLE
        self.C_LAST: str = C_LAST
        self.C_STREET_1: str = C_STREET_1
        self.C_STREET_2: str = C_STREET_2
        self.C_CITY: str = C_CITY
        self.C_STATE: str = C_STATE
        self.C_ZIP: str = C_ZIP
        self.C_PHONE: str = C_PHONE
        self.C_SINCE: str = C_SINCE
        self.C_CREDIT: str = C_CREDIT
        self.C_CREDIT_LIM: float = C_CREDIT_LIM
        self.C_DISCOUNT: float = C_DISCOUNT
        self.C_BALANCE: float = C_BALANCE
        self.C_YTD_PAYMENT: float = C_YTD_PAYMENT
        self.C_PAYMENT_CNT: int = C_PAYMENT_CNT
        self.C_DELIVERY_CNT: int = C_DELIVERY_CNT
        self.C_DATA: str = C_DATA

    def __key__(self) -> tuple[Warehouse, int, int]:
        return (self.C_W_ID, self.C_D_ID, self.C_ID)

    def get_customer(self) -> Dict:
        if not exists(self):
            raise CustomerDoesNotExist(f"Customer with id: {self} does not exist")
        data = {
            'C_ID': self.C_ID, 'C_D_ID': self.C_D_ID, 'C_W_ID': self.C_W_ID,
            'C_FIRST': self.C_FIRST, 'C_MIDDLE': self.C_MIDDLE, 'C_LAST': self.C_LAST,
            'C_STREET_1': self.C_STREET_1, 'C_STREET_2': self.C_STREET_2,
            'C_CITY': self.C_CITY, 'C_STATE': self.C_STATE, 'C_ZIP': self.C_ZIP,
            'C_PHONE': self.C_PHONE, 'C_SINCE': self.C_SINCE, 'C_CREDIT': self.C_CREDIT,
            'C_CREDIT_LIM': self.C_CREDIT_LIM, 'C_DISCOUNT': self.C_DISCOUNT,
            'C_BALANCE': self.C_BALANCE, 'C_YTD_PAYMENT': self.C_YTD_PAYMENT,
            'C_PAYMENT_CNT': self.C_PAYMENT_CNT, 'C_DELIVERY_CNT': self.C_DELIVERY_CNT,
            'C_DATA': self.C_DATA,
        }
        return data

    def pay(self, h_amount: float, d_id: int, w_id: Warehouse) -> Dict:
        if not exists(self):
            raise CustomerDoesNotExist(f"Customer with id: {self} does not exist")

        self.C_BALANCE = float(self.C_BALANCE) - h_amount
        self.C_YTD_PAYMENT = float(self.C_YTD_PAYMENT) + h_amount
        self.C_PAYMENT_CNT = int(self.C_PAYMENT_CNT) + 1

        if self.C_CREDIT == "BC":
            new_data = f"{self.C_ID} {self.C_D_ID} {self.C_W_ID} {d_id} {w_id} {h_amount}"
            self.C_DATA = (new_data + "|" + self.C_DATA)

            if len(self.C_DATA) > 500:
                self.C_DATA = self.C_DATA[:500]

        data = {
            'C_ID': self.C_ID, 'C_D_ID': self.C_D_ID, 'C_W_ID': self.C_W_ID,
            'C_FIRST': self.C_FIRST, 'C_MIDDLE': self.C_MIDDLE, 'C_LAST': self.C_LAST,
            'C_STREET_1': self.C_STREET_1, 'C_STREET_2': self.C_STREET_2,
            'C_CITY': self.C_CITY, 'C_STATE': self.C_STATE, 'C_ZIP': self.C_ZIP,
            'C_PHONE': self.C_PHONE, 'C_SINCE': self.C_SINCE, 'C_CREDIT': self.C_CREDIT,
            'C_CREDIT_LIM': self.C_CREDIT_LIM, 'C_DISCOUNT': self.C_DISCOUNT,
            'C_BALANCE': self.C_BALANCE, 'C_YTD_PAYMENT': self.C_YTD_PAYMENT,
            'C_PAYMENT_CNT': self.C_PAYMENT_CNT, 'C_DELIVERY_CNT': self.C_DELIVERY_CNT,
            'C_DATA': self.C_DATA,
        }
        return data


# ──────────────────────────────────────────
# Entity: CustomerIndex
# ──────────────────────────────────────────

@entity
class CustomerIndex:
    def __init__(self, C_W_ID: Warehouse, C_D_ID: int, C_LAST: str, customers: list[Customer]):
        self.C_W_ID: Warehouse = C_W_ID
        self.C_D_ID: int = C_D_ID
        self.C_LAST: str = C_LAST
        self.customers: list[Customer] = customers

    def __key__(self) -> tuple[Warehouse, int, str]:
        return (self.C_W_ID, self.C_D_ID, self.C_LAST)

    def pay(self, h_amount: float, d_id: int, w_id: Warehouse) -> Dict:
        index = (len(self.customers) - 1) // 2
        customer = self.customers[index]
        return customer.pay(h_amount, d_id, w_id)


# ──────────────────────────────────────────
# Entity: Stock
# Key: (S_W_ID, S_I_ID)
# ──────────────────────────────────────────

@entity
class Stock:
    def __init__(self, S_I_ID: Item, S_W_ID: Warehouse, S_QUANTITY: int,
                 S_DIST_01: str, S_DIST_02: str, S_DIST_03: str, S_DIST_04: str,
                 S_DIST_05: str, S_DIST_06: str, S_DIST_07: str, S_DIST_08: str,
                 S_DIST_09: str, S_DIST_10: str, S_YTD: int, S_ORDER_CNT: int,
                 S_REMOTE_CNT: int, S_DATA: str):
        self.S_I_ID: Item = S_I_ID
        self.S_W_ID: Warehouse = S_W_ID
        self.S_QUANTITY: int = S_QUANTITY
        self.S_DIST_01: str = S_DIST_01
        self.S_DIST_02: str = S_DIST_02
        self.S_DIST_03: str = S_DIST_03
        self.S_DIST_04: str = S_DIST_04
        self.S_DIST_05: str = S_DIST_05
        self.S_DIST_06: str = S_DIST_06
        self.S_DIST_07: str = S_DIST_07
        self.S_DIST_08: str = S_DIST_08
        self.S_DIST_09: str = S_DIST_09
        self.S_DIST_10: str = S_DIST_10
        self.S_YTD: int = S_YTD
        self.S_ORDER_CNT: int = S_ORDER_CNT
        self.S_REMOTE_CNT: int = S_REMOTE_CNT
        self.S_DATA: str = S_DATA

    def __key__(self) -> tuple[Warehouse, Item]:
        return (self.S_W_ID, self.S_I_ID)

    def get_stock(self) -> dict:
        data = {
            'S_I_ID': self.S_I_ID.I_ID, 'S_W_ID': self.S_W_ID, 'S_QUANTITY': self.S_QUANTITY,
            'S_DIST_01': self.S_DIST_01, 'S_DIST_02': self.S_DIST_02, 'S_DIST_03': self.S_DIST_03,
            'S_DIST_04': self.S_DIST_04, 'S_DIST_05': self.S_DIST_05, 'S_DIST_06': self.S_DIST_06,
            'S_DIST_07': self.S_DIST_07, 'S_DIST_08': self.S_DIST_08, 'S_DIST_09': self.S_DIST_09,
            'S_DIST_10': self.S_DIST_10, 'S_YTD': self.S_YTD, 'S_ORDER_CNT': self.S_ORDER_CNT,
            'S_REMOTE_CNT': self.S_REMOTE_CNT, 'S_DATA': self.S_DATA,
        }
        return data

    def update_stock(self, index: int, o_id: int, i_id: Item,
                     w_id: Warehouse, d_id: int, i_w_id: Warehouse, o_entry_d: str, i_qty: int,
                     i_name: str, i_price: float, i_brand_generic: bool) -> Dict:

        if not exists(self):
            raise StockDoesNotExist(f"Stock with key: {self} does not exist")

        self.S_YTD += i_qty
        if self.S_QUANTITY >= i_qty + 10:
            self.S_QUANTITY -= i_qty
        else:
            self.S_QUANTITY = self.S_QUANTITY + 91 - i_qty
        self.S_ORDER_CNT += 1

        if i_w_id != w_id:
            self.S_REMOTE_CNT += 1

        if i_brand_generic:
            if "original" in self.S_DATA:
                brand_generic = "B"
            else:
                brand_generic = "G"
        else:
            brand_generic = "G"

        ol_amount = i_qty * i_price

        dist = (
            self.S_DIST_01,
            self.S_DIST_02,
            self.S_DIST_03,
            self.S_DIST_04,
            self.S_DIST_05,
            self.S_DIST_06,
            self.S_DIST_07,
            self.S_DIST_08,
            self.S_DIST_09,
            self.S_DIST_10,
        )
        s_dist_xx = dist[d_id - 1]
        ol_number = index + 1

        send_async(OrderLine(
            OL_W_ID=w_id,
            OL_D_ID=d_id,
            OL_O_ID=o_id,
            OL_I_ID=i_id,
            OL_NUMBER=ol_number,
            OL_QUANTITY=i_qty,
            OL_DELIVERY_D=o_entry_d,
            OL_SUPPLY_W_ID=i_w_id,
            OL_AMOUNT=ol_amount,
            OL_DIST_INFO=s_dist_xx
        ))

        return {
            'i_name': i_name,
            'i_price': i_price,
            'ol_amount': ol_amount,
            's_quantity': self.S_QUANTITY,
            'brand_generic': brand_generic,
        }


# ──────────────────────────────────────────
# Entity: History
# ──────────────────────────────────────────

@entity
class History:
    def __init__(self, H_C_ID: int, H_C_D_ID: int, H_C_W_ID: Warehouse,
                 H_D_ID: int, H_W_ID: Warehouse, H_DATE: str, H_AMOUNT: float, H_DATA: str):
        self.H_C_ID: int = H_C_ID
        self.H_C_D_ID: int = H_C_D_ID
        self.H_C_W_ID: Warehouse = H_C_W_ID
        self.H_D_ID: int = H_D_ID
        self.H_W_ID: Warehouse = H_W_ID
        self.H_DATE: str = H_DATE
        self.H_AMOUNT: float = H_AMOUNT
        self.H_DATA: str = H_DATA

    def __key__(self) -> tuple[Warehouse, int, int]:
        return (self.H_W_ID, self.H_D_ID, self.H_C_ID)

    def get_history(self) -> dict:
        if not exists(self):
            raise HistoryDoesNotExist(f"History with key: {self} does not exist")

        data = {
            'H_C_ID': self.H_C_ID, 'H_C_D_ID': self.H_C_D_ID, 'H_C_W_ID': self.H_C_W_ID,
            'H_D_ID': self.H_D_ID, 'H_W_ID': self.H_W_ID, 'H_DATE': self.H_DATE,
            'H_AMOUNT': self.H_AMOUNT, 'H_DATA': self.H_DATA,
        }
        return data


# ──────────────────────────────────────────
# Entity: Order
# ──────────────────────────────────────────

@entity
class Order:
    def __init__(self, O_W_ID: Warehouse, O_D_ID: int, O_ID: int, O_C_ID: int = 0, O_ENTRY_D: str = "", O_CARRIER_ID: Optional[int] = None, O_OL_CNT: int = 0, O_ALL_LOCAL: bool = True):
        self.O_W_ID: Warehouse = O_W_ID
        self.O_D_ID: int = O_D_ID
        self.O_ID: int = O_ID
        self.O_C_ID: int = O_C_ID
        self.O_ENTRY_D: str = O_ENTRY_D
        self.O_CARRIER_ID: Optional[int] = O_CARRIER_ID
        self.O_OL_CNT: int = O_OL_CNT
        self.O_ALL_LOCAL: bool = O_ALL_LOCAL

    def __key__(self) -> tuple[Warehouse, int, int]:
        return (self.O_W_ID, self.O_D_ID, self.O_ID)

    def get_order(self, c_id: int, entry_d: str, ol_cnt: int, all_local: bool) -> dict:
        data = {
            'O_W_ID': self.O_W_ID, 'O_D_ID': self.O_D_ID, 'O_ID': self.O_ID, 'O_C_ID': c_id, 'O_ENTRY_D': entry_d,
            'O_OL_CNT': ol_cnt, 'O_ALL_LOCAL': all_local,
        }
        if not exists(self):
            raise OrderDoesNotExist(f"Order with key: {self} does not exist")

        return data


# ──────────────────────────────────────────
# Entity: NewOrder
# ──────────────────────────────────────────

@entity
class NewOrder:
    def __init__(self, NO_W_ID: Warehouse, NO_D_ID: int, NO_O_ID: int):
        self.NO_W_ID: Warehouse = NO_W_ID
        self.NO_D_ID: int = NO_D_ID
        self.NO_O_ID: int = NO_O_ID

    def __key__(self) -> tuple[Warehouse, int, int]:
        return (self.NO_W_ID, self.NO_D_ID, self.NO_O_ID)

    def create(self, no_o_id: int, no_d_id: int, no_w_id: Warehouse) -> None:
        self.NO_O_ID = no_o_id
        self.NO_D_ID = no_d_id
        self.NO_W_ID = no_w_id


# ──────────────────────────────────────────
# Entity: OrderLine
# ──────────────────────────────────────────

@entity
class OrderLine:
    def __init__(
        self,
        OL_W_ID: Warehouse,
        OL_D_ID: int,
        OL_O_ID: int,
        OL_I_ID: Item,
        OL_NUMBER: int,
        OL_QUANTITY: int = 0,
        OL_DELIVERY_D: Optional[str] = None,
        OL_SUPPLY_W_ID: Optional[Warehouse] = None,
        OL_DIST_INFO: str = "",
        OL_AMOUNT: float = 0.0
    ):
        self.OL_W_ID = OL_W_ID
        self.OL_D_ID = OL_D_ID
        self.OL_O_ID = OL_O_ID
        self.OL_I_ID = OL_I_ID
        self.OL_NUMBER = OL_NUMBER
        self.OL_QUANTITY = OL_QUANTITY
        self.OL_DELIVERY_D = OL_DELIVERY_D
        self.OL_SUPPLY_W_ID = OL_SUPPLY_W_ID
        self.OL_DIST_INFO = OL_DIST_INFO
        self.OL_AMOUNT = OL_AMOUNT

    def __key__(self) -> tuple[Warehouse, int, int, int]:
        return (self.OL_W_ID, self.OL_D_ID, self.OL_O_ID, self.OL_NUMBER)

    def get_order_line(self) -> dict:
        if not exists(self):
            raise OrderLineDoesNotExist(f"OrderLine with key: {self} does not exist")
        data = {
            'OL_W_ID': self.OL_W_ID, 'OL_D_ID': self.OL_D_ID, 'OL_O_ID': self.OL_O_ID,
            'OL_I_ID': self.OL_I_ID.I_ID, 'OL_NUMBER': self.OL_NUMBER, 'OL_QUANTITY': self.OL_QUANTITY,
            'OL_DELIVERY_D': self.OL_DELIVERY_D, 'OL_SUPPLY_W_ID': self.OL_SUPPLY_W_ID if self.OL_SUPPLY_W_ID else None,
            'OL_DIST_INFO': self.OL_DIST_INFO, 'OL_AMOUNT': self.OL_AMOUNT,
        }
        return data


# ──────────────────────────────────────────
# Entity: NewOrderTxn  (transaction coordinator)
# ──────────────────────────────────────────

@entity
class NewOrderTxn:
    def __init__(self, txn_id: str):
        self.txn_id: str = txn_id

    def __key__(self) -> str:
        return self.txn_id

    def new_order(self, params: dict) -> str:

        w_id: Warehouse = params["W_ID"]
        d_id: int = params["D_ID"]
        c_id: int = params["C_ID"]
        o_entry_d: str = params["O_ENTRY_D"]
        i_ids: list[Item] = params["I_IDS"]
        i_w_ids: list[Warehouse] = params["I_W_IDS"]
        i_qtys: list[int] = params["I_QTYS"]

        assert len(i_ids) > 0
        # assert len(i_ids) == len(i_w_ids) == len(i_qtys)

        all_local = True
        for item_w_id in i_w_ids:
            if item_w_id != w_id:
                all_local = False
                break


        district = get_entity_by_key(District, (w_id, d_id))
        customer = get_entity_by_key(Customer, (w_id, d_id, c_id))


        warehouse_data, district_data, customer_data = gather(w_id.get_warehouse(), district.get_district(
            w_id, d_id, c_id, o_entry_d, i_ids, i_qtys, i_w_ids, all_local), customer.get_customer()
        )

        item_replies = gather(*[item.get_item(
            i, w_id, d_id, o_entry_d, i_qtys[i], i_w_ids[i], district_data['D_NEXT_O_ID']
        ) for i, item in enumerate(i_ids)])


        total = sum(item_reply['ol_amount'] for item_reply in item_replies)

        # Pack the final response.
        w_tax: float = warehouse_data['W_TAX']
        d_tax: float = district_data['D_TAX']
        total = total * (1 - customer_data['C_DISCOUNT']) * (1 + w_tax + d_tax)
        o_id = district_data['D_NEXT_O_ID']

        item_str = ";".join(
            f"{r['i_name']},{r['s_quantity']},{r['brand_generic']},{r['i_price']:.2f},{r['ol_amount']:.2f}"
            for r in item_replies
        )

        return (
            f"NO|C_ID={customer_data['C_ID']},C_LAST={customer_data['C_LAST']},"
            f"C_CREDIT={customer_data['C_CREDIT']},"
            f"C_DISCOUNT={customer_data['C_DISCOUNT']:.4f},W_TAX={w_tax:.4f},D_TAX={d_tax:.4f},"
            f"O_ID={o_id},O_ENTRY_D={o_entry_d},N_ITEMS={len(item_replies)},"
            f"TOTAL={total:.2f},ITEMS=[{item_str}]"
        )


# ──────────────────────────────────────────
# Entity: PaymentTxn  (transaction coordinator)
# ──────────────────────────────────────────

@entity
class PaymentTxn:
    def __init__(
        self,
        txn_id: str,
        w_id: Warehouse,
        c_w_id: Warehouse,
        d_id: int = 0,
        c_d_id: int = 0,
        h_amount: float = 0.0,
        h_date: str = "",
    ):
        self.txn_id: str = txn_id
        self.W_ID: Warehouse = w_id
        self.D_ID: int = d_id
        self.C_W_ID: Warehouse = c_w_id
        self.C_D_ID: int = c_d_id
        self.C_ID: Optional[int] = None
        self.H_AMOUNT: float = h_amount
        self.H_DATE: str = h_date

    def __key__(self) -> str:
        return self.txn_id

    def payment(self, params: dict) -> str:
        w_id: Warehouse = params["W_ID"]
        d_id: int = int(params["D_ID"])
        h_amount: float = params["H_AMOUNT"]
        c_w_id: Warehouse = params["C_W_ID"]
        c_d_id: int = int(params["C_D_ID"])

        c_id: Optional[int] = int(params["C_ID"]) if params.get("C_ID") is not None else None
        c_last: Optional[str] = params.get("C_LAST")
        h_date: str = params["H_DATE"]

        self.W_ID = w_id
        self.D_ID = d_id
        self.C_ID = c_id
        self.C_W_ID = c_w_id
        self.C_D_ID = c_d_id
        self.H_DATE = h_date
        self.H_AMOUNT = h_amount

        if c_id is not None:
            customer = get_entity_by_key(Customer, (w_id, c_d_id, c_id))
            customer_data = customer.pay(h_amount, d_id, w_id)
        else:
            customer_idx = get_entity_by_key(CustomerIndex, (c_w_id, c_d_id, c_last))
            customer_data = customer_idx.pay(h_amount, d_id, w_id)

        district = get_entity_by_key(District, (w_id, d_id))

        district_data, warehouse_data = gather(
                district.pay(h_amount), w_id.pay(h_amount)
            )


        # Build history record and persist it.
        h_data = f"{warehouse_data['W_NAME']}    {district_data['D_NAME']}"
        send_async(History(
            customer_data['C_ID'], self.C_D_ID, self.C_W_ID,
            self.D_ID, self.W_ID, self.H_DATE, self.H_AMOUNT, h_data
        ))

        if customer_data['C_CREDIT'] == "BC":
            c_data_str = f",C_DATA={customer_data['C_DATA'][:200]}"
        else:
            c_data_str = ""

        return (
            f"P|W_ID={self.W_ID},D_ID={district_data['D_ID']},C_ID={customer_data['C_ID']},"
            f"C_D_ID={customer_data['C_D_ID']},C_W_ID={customer_data['C_W_ID']},"
            f"C_NAME={customer_data['C_FIRST']} {customer_data['C_MIDDLE']} {customer_data['C_LAST']},"
            f"C_BAL={customer_data['C_BALANCE']:.2f},C_DISCOUNT={customer_data['C_DISCOUNT']:.4f},"
            f"C_CREDIT={customer_data['C_CREDIT']},W_TAX={warehouse_data['W_TAX']:.4f},"
            f"D_TAX={district_data['D_TAX']:.4f},H_AMOUNT={self.H_AMOUNT:.2f},"
            f"H_DATE={self.H_DATE}{c_data_str}"
        )
