"""领域对象测试。"""

from dataclasses import FrozenInstanceError
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from quant_forge.domain.models import MarketPremiumSnapshot

CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


def test_market_premium_snapshot_keeps_four_premium_metrics() -> None:
  """快照必须完整保存四项短线溢价指标。"""
  snapshot = MarketPremiumSnapshot(
    trade_date=date(2026, 9, 7),
    first_board_premium_pct=2.1,
    second_board_premium_pct=1.0,
    multi_board_premium_pct=0.5,
    limit_up_premium_pct=2.2,
    source="example",
    collected_at=datetime(2026, 9, 8, 8, 30, tzinfo=CHINA_TIMEZONE),
  )

  assert snapshot.first_board_premium_pct == 2.1
  assert snapshot.second_board_premium_pct == 1.0
  assert snapshot.multi_board_premium_pct == 0.5
  assert snapshot.limit_up_premium_pct == 2.2


def test_market_premium_snapshot_is_immutable() -> None:
  """盘前输入必须不可变，避免计算途中被修改。"""
  snapshot = MarketPremiumSnapshot(
    trade_date=date(2026, 9, 7),
    first_board_premium_pct=2.1,
    second_board_premium_pct=1.0,
    multi_board_premium_pct=0.5,
    limit_up_premium_pct=2.2,
    source="example",
    collected_at=datetime(2026, 9, 8, 8, 30, tzinfo=CHINA_TIMEZONE),
  )

  with pytest.raises(FrozenInstanceError):
    snapshot.first_board_premium_pct = 9.9  # type: ignore[misc]
