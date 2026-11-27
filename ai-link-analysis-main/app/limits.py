from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from math import ceil
from time import monotonic

from app.config import Settings
from app.utils.validators import AnalysisError


class AnalysisLimits:
    def __init__(
        self, settings: Settings, clock: Callable[[], float] = monotonic
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.recent: deque[tuple[float, str]] = deque()
        self.daily: deque[float] = deque()
        self.in_flight = 0

    def reject(self, delay: float) -> None:
        raise AnalysisError(
            429,
            "analysis_limit",
            "Analysis capacity is limited. Retry later.",
            headers={"Retry-After": str(max(1, ceil(delay)))},
        )

    @contextmanager
    def reserve(self, caller: str) -> Iterator[None]:
        now = self.clock()
        while self.recent and now - self.recent[0][0] >= 60:
            self.recent.popleft()
        while self.daily and now - self.daily[0] >= 86_400:
            self.daily.popleft()
        if len(self.daily) >= self.settings.analysis_per_day:
            self.reject(86_400 - (now - self.daily[0]))
        if len(self.recent) >= self.settings.analysis_per_minute:
            self.reject(60 - (now - self.recent[0][0]))
        caller_requests = [time for time, client in self.recent if client == caller]
        if len(caller_requests) >= self.settings.analysis_per_caller_per_minute:
            self.reject(60 - (now - caller_requests[0]))
        if self.in_flight >= self.settings.analysis_max_in_flight:
            self.reject(5)
        self.recent.append((now, caller))
        self.daily.append(now)
        self.in_flight += 1
        try:
            yield
        finally:
            self.in_flight -= 1
