"""
DynamoDBの軽量インメモリfake。motoがこの環境ではcryptography/pyo3依存で動かないため、
handler.pyが実際に使う範囲(get_item/put_item/delete_item/scan/query、
ConditionExpression="attribute_not_exists(<key>)")だけをサポートする自前実装。
"""


class ConditionalCheckFailedException(Exception):
    pass


class FakeTable:
    def __init__(self, hash_key: str, range_key: str | None = None):
        self.hash_key = hash_key
        self.range_key = range_key
        self._items: dict[tuple, dict] = {}

    def _pk(self, item_or_key: dict) -> tuple:
        if self.range_key:
            return (item_or_key[self.hash_key], item_or_key[self.range_key])
        return (item_or_key[self.hash_key],)

    def get_item(self, Key: dict):
        item = self._items.get(self._pk(Key))
        return {"Item": item} if item is not None else {}

    def put_item(self, Item: dict, ConditionExpression: str | None = None):
        pk = self._pk(Item)
        condition_key = self.range_key or self.hash_key
        if ConditionExpression == f"attribute_not_exists({condition_key})" and pk in self._items:
            raise ConditionalCheckFailedException()
        self._items[pk] = Item

    def delete_item(self, Key: dict):
        self._items.pop(self._pk(Key), None)

    def scan(self):
        return {"Items": list(self._items.values())}

    def query(self, KeyConditionExpression=None, **kwargs):
        """boto3.dynamodb.conditions.Key(...).eq(...)相当を最小限サポート。
        HASH keyの完全一致検索のみ対応(このプロジェクトで使う範囲)。"""
        hash_value = KeyConditionExpression._values[1]
        items = [v for k, v in self._items.items() if k[0] == hash_value]
        return {"Items": items}


class _Exceptions:
    ConditionalCheckFailedException = ConditionalCheckFailedException


class _Client:
    exceptions = _Exceptions()


class _Meta:
    client = _Client()


class FakeDynamoDBResource:
    """テーブルごとの主キー(HASH/RANGE)を事前に登録しておく(実テーブル定義相当)"""

    def __init__(self, key_schemas: dict[str, tuple]):
        """key_schemas: {テーブル名: (hash_key, range_keyまたはNone)}"""
        self._key_schemas = key_schemas
        self._tables: dict[str, FakeTable] = {}
        self.meta = _Meta()

    def Table(self, name: str) -> FakeTable:
        if name not in self._tables:
            hash_key, range_key = self._key_schemas[name]
            self._tables[name] = FakeTable(hash_key, range_key)
        return self._tables[name]
