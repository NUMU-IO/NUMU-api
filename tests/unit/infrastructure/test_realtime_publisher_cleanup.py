import anyio

from src.infrastructure.realtime.redis_pubsub import RealtimePublisher


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def unsubscribe(self, channel: str) -> None:
        await anyio.sleep(0)
        self.calls.append("unsubscribe")

    async def aclose(self) -> None:
        await anyio.sleep(0)
        self.calls.append("aclose")


async def test_cleanup_completes_inside_a_cancelled_stream() -> None:
    pubsub, client = _Recorder(), _Recorder()
    publisher = RealtimePublisher(redis_client=client)

    with anyio.CancelScope() as scope:
        scope.cancel()
        await publisher.unsubscribe("store:x:notifications", pubsub)
        await publisher.aclose()

    assert pubsub.calls == ["unsubscribe", "aclose"]
    assert client.calls == ["aclose"]
