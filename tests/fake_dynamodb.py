"""
DynamoDBの軽量インメモリfake。motoがこの環境ではcryptography/pyo3依存で動かないため、
handler.pyが実際に使う範囲(get_item/put_item/delete_item/scan、ConditionExpression=
"attribute_not_exists(<key>)")だけをサポートする自前実装。
"""


class ConditionalCheckFailedException(Exception):
    pass


class FakeTable:
    def __init__(self, key_name: str):
        self.key_name = key_name
        self._items: dict[str, dict] = {}

    def get_item(self, Key: dict):
        pk = Key[self.key_name]
        item = self._items.get(pk)
        return {"Item": item} if item is not None else {}

    def put_item(self, Item: dict, ConditionExpression: str | None = None):
        pk = Item[self.key_name]
        if ConditionExpression == f"attribute_not_exists({self.key_name})" and pk in self._items:
            raise ConditionalCheckFailedException()
        self._items[pk] = Item

    def delete_item(self, Key: dict):
        self._items.pop(Key[self.key_name], None)

    def scan(self):
        return {"Items": list(self._items.values())}


class _Exceptions:
    ConditionalCheckFailedException = ConditionalCheckFailedException


class _Client:
    exceptions = _Exceptions()


class _Meta:
    client = _Client()


class FakeDynamoDBResource:
    """テーブルごとの主キー名を事前に登録しておく(実テーブル定義相当)"""

    def __init__(self, key_names: dict[str, str]):
        self._key_names = key_names
        self._tables: dict[str, FakeTable] = {}
        self.meta = _Meta()

    def Table(self, name: str) -> FakeTable:
        if name not in self._tables:
            self._tables[name] = FakeTable(self._key_names[name])
        return self._tables[name]
