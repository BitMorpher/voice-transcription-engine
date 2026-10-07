"""Bound active sessions without enqueueing the entire batch."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context


def run_sessions(items, workers, process, record, control):
    """Run up to workers sessions, then drain safely before returning/raising.

    Each submission receives its own context but the same provider control and
    reporter. Only the coordinator records rows. No new session is submitted
    once admission stops; the caller records the remaining selection honestly.
    """
    pending = {}
    iterator = iter(items)
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='interview')
    failure = None
    try:
        while True:
            while len(pending) < workers and not control.reason and not control.cancelled.is_set():
                item = next(iterator, None)
                if item is None:
                    break
                future = executor.submit(copy_context().run, process, item)
                pending[future] = item
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda future: pending[future]['position']):
                pending.pop(future)
                record(future.result())
            if control.cancelled.is_set():
                raise KeyboardInterrupt()
    except BaseException as error:
        failure = error
        control.cancel()
    finally:
        # Keep batch/job locks and log open until workers stop. Cancelling a
        # Future cannot interrupt synchronous SDK I/O already running.
        for future in pending:
            future.cancel()
        # Repeated signals must not release the batch lock while writes are
        # still running. Wait for Futures (the actual work), then join threads.
        while pending:
            try:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in sorted(done, key=lambda future: pending[future]['position']):
                    pending.pop(future)
                    if future.cancelled():
                        continue
                    try:
                        record(future.result())
                    except BaseException as error:
                        failure = failure or error
                        control.cancel()
            except KeyboardInterrupt as error:
                failure = failure or error
                control.cancel()
        try:
            executor.shutdown(wait=True, cancel_futures=True)
        except KeyboardInterrupt as error:
            # All submitted work is finished, so no artifact writes remain.
            failure = failure or error
    if failure is not None:
        raise failure
