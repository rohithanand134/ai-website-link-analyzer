from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

stage_times: ContextVar[dict[str, float] | None] = ContextVar(
    "stage_times", default=None
)


@contextmanager
def measure(stage: str):
    timings = stage_times.get()
    started = perf_counter()
    try:
        yield
    finally:
        if timings is not None:
            timings[stage] = timings.get(stage, 0.0) + (perf_counter() - started) * 1000
