from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class ReplayTrade:
    session: str
    strategy: str
    eligible: bool
    gross_pnl_usd: Decimal
    friction_usd: Decimal

    @property
    def net_pnl_usd(self) -> Decimal:
        return self.gross_pnl_usd - self.friction_usd


@dataclass(frozen=True)
class BatchReplayReport:
    sessions: int
    eligible_trades: int
    rejected_trades: int
    gross_pnl_usd: Decimal
    friction_usd: Decimal
    net_pnl_usd: Decimal
    friction_share_of_gross_profit: Decimal | None


def summarize_batch_replay(trades: Iterable[ReplayTrade]) -> BatchReplayReport:
    values = tuple(trades)
    eligible = tuple(item for item in values if item.eligible)
    gross = sum((item.gross_pnl_usd for item in eligible), Decimal("0"))
    friction = sum((item.friction_usd for item in eligible), Decimal("0"))
    gross_profit = sum((item.gross_pnl_usd for item in eligible if item.gross_pnl_usd > 0), Decimal("0"))
    return BatchReplayReport(
        len({item.session for item in values}), len(eligible), len(values) - len(eligible),
        gross, friction, gross - friction,
        friction / gross_profit if gross_profit > 0 else None,
    )
