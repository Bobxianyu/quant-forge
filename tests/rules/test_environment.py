"""市场环境、风格和基础权限规则测试。"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from quant_forge.domain.models import (
  MarketEnvironment,
  MarketPremiumSnapshot,
  MarketStyle,
  PermissionGrade,
  RecoveryStrength,
)
from quant_forge.rules.environment import assess_environment

CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


def make_snapshot(
  first_board: float,
  second_board: float,
  multi_board: float,
  limit_up: float,
) -> MarketPremiumSnapshot:
  """创建只改变四项溢价指标的测试快照。"""
  return MarketPremiumSnapshot(
    trade_date=date(2026, 9, 7),
    first_board_premium_pct=first_board,
    second_board_premium_pct=second_board,
    multi_board_premium_pct=multi_board,
    limit_up_premium_pct=limit_up,
    source="test",
    collected_at=datetime(2026, 9, 8, 8, 30, tzinfo=CHINA_TIMEZONE),
  )


def test_classifies_ebb_tide_when_all_metrics_are_negative() -> None:
  """四项全部为负必须进入全面退潮并禁止开仓。"""
  result = assess_environment(make_snapshot(-0.1, -0.2, -0.3, -0.4))

  assert result.environment is MarketEnvironment.EBB_TIDE
  assert result.market_style is MarketStyle.BROAD_EBB_TIDE
  assert result.permission_grade is PermissionGrade.D
  assert result.recovery_strength is None


def test_classifies_profit_effect_when_three_metrics_are_positive() -> None:
  """整体涨停为正且至少三项为正时进入赚钱效应。"""
  result = assess_environment(make_snapshot(2.5, 1.0, 0.5, 2.2))

  assert result.environment is MarketEnvironment.PROFIT_EFFECT
  assert result.market_style is MarketStyle.FIRST_BOARD_ARBITRAGE
  assert result.permission_grade is PermissionGrade.A


def test_treats_zero_as_neutral_and_returns_weak_recovery() -> None:
  """零值不计入正负数量，冲突信号按弱修复处理。"""
  result = assess_environment(make_snapshot(0.0, 0.0, -0.1, 0.0))

  assert result.environment is MarketEnvironment.RECOVERY
  assert result.recovery_strength is RecoveryStrength.WEAK
  assert result.permission_grade is PermissionGrade.C


def test_classifies_high_low_switch_before_first_board_arbitrage() -> None:
  """高低切规则优先于普通首板套利规则。"""
  result = assess_environment(make_snapshot(3.0, -0.2, -0.3, 1.0))

  assert result.market_style is MarketStyle.HIGH_LOW_SWITCH
  assert result.permission_grade is PermissionGrade.C


def test_classifies_relay_when_second_and_multi_board_are_strong() -> None:
  """二板和多板明显强、首板一般时识别为接力行情。"""
  result = assess_environment(make_snapshot(1.0, 3.0, 2.5, 1.2))

  assert result.market_style is MarketStyle.RELAY
  assert result.permission_grade is PermissionGrade.B


def test_grade_b_includes_zero_first_board_boundary() -> None:
  """首板恰好为零且整体涨停为正时仍属于 B 级。"""
  result = assess_environment(make_snapshot(0.0, -0.1, -0.1, 0.1))

  assert result.permission_grade is PermissionGrade.B
  assert result.environment is MarketEnvironment.RECOVERY
