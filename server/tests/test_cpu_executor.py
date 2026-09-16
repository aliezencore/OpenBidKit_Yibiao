import asyncio
import threading
import time

from app.infra.tasks.cpu import CpuExecutor


async def test_run_returns_function_result():
    executor = CpuExecutor(max_workers=2)

    result = await executor.run(lambda a, b: a + b, 2, 3)

    assert result == 5
    executor.shutdown()


async def test_run_executes_off_the_event_loop_thread():
    executor = CpuExecutor(max_workers=2)
    caller = threading.get_ident()

    worker = await executor.run(threading.get_ident)

    assert worker != caller
    executor.shutdown()


async def test_run_limits_concurrency_to_max_workers():
    executor = CpuExecutor(max_workers=2)
    lock = threading.Lock()
    state = {"running": 0, "peak": 0}

    def work():
        with lock:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
        time.sleep(0.05)
        with lock:
            state["running"] -= 1

    await asyncio.gather(*(executor.run(work) for _ in range(6)))

    assert state["peak"] == 2, "线程池不得超过 max_workers"
    executor.shutdown()


async def test_run_propagates_exception():
    executor = CpuExecutor(max_workers=1)

    def boom():
        raise ValueError("解析失败")

    try:
        await executor.run(boom)
    except ValueError as error:
        assert str(error) == "解析失败"
    else:
        raise AssertionError("异常应当传播")
    finally:
        executor.shutdown()
