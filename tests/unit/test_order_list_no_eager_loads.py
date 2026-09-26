"""Order-list reads skip the four selectin relationships _to_entity never reads."""

import asyncio
import uuid

from src.infrastructure.repositories.order_repository import OrderRepository

EXPECTED = {"store", "customer", "invoice", "coupon"}


class _Captured:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return []

        return _Result()


def _noloaded(statement) -> set[str]:
    """Relationship names a statement noloads (``OrderModel.<name>``)."""
    names = set()
    for opt in statement._with_options:
        for ctx in opt.context:
            if dict(ctx.strategy or ()).get("lazy") == "noload":
                # "... -> OrderModel.store -> Mapper[...]"
                names.add(str(ctx.path).split(" -> ")[1].split(".")[1])
    return names


def test_list_reads_noload_unused_relationships():
    session = _Captured()
    repo = OrderRepository(session)
    store = uuid.uuid4()
    asyncio.run(repo.get_by_store(store, 0, 20))
    asyncio.run(repo.search(store, "ORD", 0, 20))
    assert [_noloaded(s) for s in session.statements] == [EXPECTED, EXPECTED]
