"""同步 → 异步桥接器（生产落地 P0-1.1）。

背景：业务工具层（agent/tools/ecom/store.py）与登录校验（security/auth_service.py）
是同步函数，而数据访问层（db/repository.py，SQLAlchemy async）是异步接口。
本模块在独立后台线程中维护一个专属事件循环，让同步代码可以安全地执行
异步数据库操作：

    from db.sync_bridge import run_async
    users = run_async(repository.list_all_users())

注意：
- run_async 会阻塞调用线程直到协程完成（带超时保护）。在 Agent 的 async
  execute() 中调用时会短暂阻塞事件循环，本地数据库延迟（毫秒级）下可接受；
  这是"只增不改"约束下的折中——彻底非阻塞需要把全部业务工具改为 async。
- 若 DATA_BACKEND=json（默认），本模块不会被加载，零开销。
"""

import asyncio
import threading

# 单条数据库操作的最长等待时间（秒），防止慢查询拖死调用方
BRIDGE_TIMEOUT = float(__import__("os").getenv("DB_BRIDGE_TIMEOUT", "10"))

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()


def _start_loop() -> None:
    """在后台线程中启动专属事件循环（只启动一次）。"""
    global _loop, _thread
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)  # 该线程内 loop 可运行
    _loop.run_forever()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """获取（必要时启动）后台事件循环。"""
    global _thread
    with _lock:
        if _loop is None or not _loop.is_running():
            _thread = threading.Thread(
                target=_start_loop, name="db-sync-bridge", daemon=True
            )
            _thread.start()
            # 等待循环就绪（_start_loop 设置 _loop 在 run_forever 之前，
            # 但线程调度需要一点时间；用简短自旋等待）
            while _loop is None or not _loop.is_running():
                pass
        return _loop


def run_async(coro, timeout: float | None = None):
    """在后台事件循环中执行协程并同步等待结果。

    Args:
        coro: 待执行的协程对象
        timeout: 最长等待秒数（默认 BRIDGE_TIMEOUT）

    Returns:
        协程的返回值

    Raises:
        TimeoutError: 超时
        Exception: 协程内抛出的异常原样透传
    """
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout or BRIDGE_TIMEOUT)
    except TimeoutError:
        future.cancel()
        raise TimeoutError(f"数据库操作超时（>{timeout or BRIDGE_TIMEOUT}s）")
