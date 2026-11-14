"""Short-horizon load forecasting: EWMA smoothing + least-squares trend.

The scaler reports max(current, forecast) to KEDA, so replicas rise while a ramp
is still building (forecast > current) but never drop just because the trend
points down - scale-down stays with the HPA stabilization window."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


def ewma(series: Sequence[float], alpha: float) -> list[float]:
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")
    out: list[float] = []
    for x in series:
        out.append(x if not out else alpha * x + (1 - alpha) * out[-1])
    return out


def linear_fit(ys: Sequence[float]) -> tuple[float, float]:
    """Least-squares fit y = slope * x + intercept over x = 0..n-1."""
    n = len(ys)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return 0.0, float(ys[0])
    mean_x = (n - 1) / 2
    mean_y = sum(ys) / n
    sxx = sum((i - mean_x) ** 2 for i in range(n))
    sxy = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(ys))
    slope = sxy / sxx
    return slope, mean_y - slope * mean_x


@dataclass(frozen=True)
class Forecast:
    current: float
    forecast: float

    @property
    def value(self) -> float:
        """What KEDA sees."""
        return max(self.current, self.forecast)


def forecast(
    series: Sequence[float],
    step_seconds: float,
    horizon_seconds: float,
    alpha: float = 0.5,
) -> Forecast:
    clean = [x for x in series if math.isfinite(x)]
    if not clean:
        return Forecast(0.0, 0.0)
    current = max(clean[-1], 0.0)
    if len(clean) < 3:
        return Forecast(current, current)

    smoothed = ewma(clean, alpha)
    slope, intercept = linear_fit(smoothed)
    steps_ahead = horizon_seconds / step_seconds
    predicted = intercept + slope * (len(clean) - 1 + steps_ahead)
    return Forecast(current, max(predicted, 0.0))


def replicas_for(value: float, target_per_replica: float) -> int:
    """What the HPA will compute for an AverageValue external metric (for logs/tests)."""
    return max(1, math.ceil(value / target_per_replica))
