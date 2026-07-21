from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import product
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class SensitivityResult:
    parameters: dict[str, object]
    observations: int
    net_pnl_usd: Decimal
    expectancy_usd: Decimal | None


def parameter_grid(values: Mapping[str, Sequence[object]]) -> tuple[dict[str, object], ...]:
    names = tuple(sorted(values))
    if not names or any(not values[name] for name in names):
        raise ValueError("PARAMETER_GRID_EMPTY")
    return tuple(dict(zip(names, combination)) for combination in product(*(values[name] for name in names)))


def run_sensitivity(
    grid: Sequence[dict[str, object]],
    evaluator: Callable[[dict[str, object]], Sequence[Decimal]],
) -> tuple[SensitivityResult, ...]:
    results: list[SensitivityResult] = []
    for parameters in grid:
        pnl = tuple(evaluator(parameters))
        net = sum(pnl, Decimal("0"))
        results.append(SensitivityResult(dict(parameters), len(pnl), net, net / Decimal(len(pnl)) if pnl else None))
    return tuple(results)
