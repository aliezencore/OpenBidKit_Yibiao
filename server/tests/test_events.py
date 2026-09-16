import asyncio
import json

from app.infra.tasks.events import EventBus, sse_event_stream


async def test_publish_reaches_subscriber():
    bus = EventBus()
    queue = bus.subscribe("t1")

    bus.publish("t1", {"type": "progress", "progress": 10})

    assert await asyncio.wait_for(queue.get(), timeout=1) == {"type": "progress", "progress": 10}


async def test_publish_without_subscriber_is_noop():
    bus = EventBus()

    bus.publish("nobody", {"type": "log"})

    assert bus.subscriber_count("nobody") == 0


async def test_unsubscribe_removes_queue():
    bus = EventBus()
    queue = bus.subscribe("t1")

    bus.unsubscribe("t1", queue)

    assert bus.subscriber_count("t1") == 0


async def test_sse_stream_serializes_events_as_json():
    bus = EventBus()
    stream = sse_event_stream(bus, "t1", heartbeat_seconds=60)
    first = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)
    bus.publish("t1", {"type": "status", "status": "running"})

    chunk = await asyncio.wait_for(first, timeout=1)

    assert chunk == 'data: {"type": "status", "status": "running"}\n\n'
    await stream.aclose()


async def test_sse_stream_emits_heartbeat_when_idle():
    bus = EventBus()
    stream = sse_event_stream(bus, "t1", heartbeat_seconds=0.01)

    chunk = await asyncio.wait_for(stream.__anext__(), timeout=1)

    assert chunk == ": ping\n\n"
    await stream.aclose()
