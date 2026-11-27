import pytest

from app.config import Settings
from app.limits import AnalysisLimits
from app.utils.validators import AnalysisError


def test_global_limits_apply_across_callers_and_release_capacity():
    now = 0.0
    limits = AnalysisLimits(
        Settings(analysis_max_in_flight=1, analysis_per_minute=1, analysis_per_day=2),
        lambda: now,
    )
    with limits.reserve("caller-1"):
        now = 61
        with pytest.raises(AnalysisError) as error, limits.reserve("caller-2"):
            pytest.fail("Concurrency limit should reject a second request")
        assert error.value.headers == {"Retry-After": "5"}
    with limits.reserve("caller-2"):
        pass
    now = 122
    with pytest.raises(AnalysisError) as error, limits.reserve("caller-3"):
        pytest.fail("Daily limit should reject a third request")
    assert error.value.status == 429
    now = 86_461
    with limits.reserve("caller-3"):
        pass
