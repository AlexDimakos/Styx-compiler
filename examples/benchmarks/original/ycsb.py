from styx_compiler.core import entity, send_async

class NotEnoughCredit(Exception):
    pass

@entity
class YCSB:
    def __init__(self, key: str):
        self.key: str = key
        self.value: int = 1_000_000

    def __key__(self):
        return self.key

    def get_key(self) -> str:
        return self.key

    def get_value(self) -> int: return self.value

    def set_value(self, value: int):
        self.value = value

    def read(self) -> tuple[YCSB, int]:
        return self, self.value

    def update(self) -> tuple[YCSB, int]:
        self.value += 1
        return self, self.value


    def transfer(self, key_b: YCSB) -> tuple[YCSB, int]:

        send_async(key_b.update())

        self.value -= 1
        if self.value < 0:
            raise NotEnoughCredit(f"Not enough credit for user: {self}")

        return self, self.value
