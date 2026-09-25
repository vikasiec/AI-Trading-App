"""Portfolio-level risk checks.

RiskGovernor.validate_trade() reasons about ONE order at a time -- it has
no idea how many other positions are already open, or how much capital
is already deployed across them. This module is the aggregate check,
run against the live PositionStore before a new entry is allowed to
reach RiskGovernor at all:

  - max_concurrent_positions: a hard cap on how many symbols can be
    held open simultaneously, regardless of how attractive a new signal
    looks -- concentration risk isn't visible to a per-order check.
  - max_deployed_capital_pct: the total capital tied up in open
    positions, as a fraction of equity, capped independently of any
    single position's own sizing limit.

Both caps exist because RiskGovernor.size_order() only knows how to
size the position in front of it; nothing before this module stopped
the bot from opening N of those in parallel and blowing through an
aggregate limit no single order ever violated on its own.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import RiskConfig
from .positions import PositionStore
from .sectors import sector_for


@dataclass(frozen=True)
class PortfolioRiskResult:
    approved: bool
    reason: str


def check_portfolio_risk(
    store: PositionStore,
    risk_cfg: RiskConfig,
    equity: float,
    candidate_capital: float,
    candidate_symbol: str = "",
) -> PortfolioRiskResult:
    """candidate_capital is the capital the NEW position would tie up,
    checked against the room left after existing open positions.
    """
    open_positions = store.list_open()

    if len(open_positions) >= risk_cfg.max_concurrent_positions:
        return PortfolioRiskResult(
            False, f"max_concurrent_positions_reached ({len(open_positions)}/{risk_cfg.max_concurrent_positions})"
        )

    deployed = sum(p.entry_price * p.qty for p in open_positions)
    if equity <= 0:
        return PortfolioRiskResult(False, "invalid_equity")

    projected_pct = (deployed + candidate_capital) / equity
    if projected_pct > risk_cfg.max_deployed_capital_pct:
        return PortfolioRiskResult(
            False,
            f"max_deployed_capital_exceeded ({projected_pct:.2%} > {risk_cfg.max_deployed_capital_pct:.2%})",
        )

    if candidate_symbol:
        sector = sector_for(candidate_symbol)
        sector_deployed = 0.0
        for p in open_positions:
            if sector_for(p.symbol) == sector:
                sector_deployed += p.entry_price * p.qty
        sector_pct = (sector_deployed + candidate_capital) / equity
        if sector_pct > risk_cfg.max_sector_capital_pct:
            return PortfolioRiskResult(
                False,
                f"max_sector_capital_exceeded ({sector} {sector_pct:.2%} > {risk_cfg.max_sector_capital_pct:.2%})",
            )

    return PortfolioRiskResult(True, "approved")
