"""Shared bounded thread pool for parallel data fetching.

Used by pages that fan out many independent network/cache calls (show
seasons, calendar shows, watchlist fully-watched checks, search).  The
pool is deliberately small so it composes with the poster-loading
threads and page threads without exhausting resources.

Executor tasks must never touch GTK widgets; they only fetch data and
return it; callers marshal results back to the main thread.
"""

from concurrent.futures import ThreadPoolExecutor

_EXECUTOR: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(
            max_workers=6, thread_name_prefix="ciak-fetch"
        )
    return _EXECUTOR


def submit(fn, *args, **kwargs):
    return _pool().submit(fn, *args, **kwargs)


def shutdown(wait: bool = False) -> None:
    global _EXECUTOR
    if _EXECUTOR is not None:
        _EXECUTOR.shutdown(wait=wait)
        _EXECUTOR = None
