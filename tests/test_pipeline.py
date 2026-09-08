"""完整盘前决策管道测试。"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from quant_forge.domain.models import (
  MarketEnvironment,
  MarketPremiumSnapshot,
  NewsEvent,
  PermissionGrade,
  RiskLevel,
  SectorSnapshot,
)
from quant_forge.pipeline import build_pre_market_decision

CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")
REPORT_DATE = date(2026, 9, 8)
CUTOFF_AT = datetime(2026, 9, 8, 8, 50, tzinfo=CHINA_TIMEZONE)


def make_market(*, collected_at: datetime | None = None) -> MarketPremiumSnapshot:
  """创建能够产生 A 级权限的核心快照。"""
  return MarketPremiumSnapshot(
    trade_date=date(2026, 9, 7),
    first_board_premium_pct=2.5,
    second_board_premium_pct=1.0,
    multi_board_premium_pct=0.5,
    limit_up_premium_pct=2.2,
    source="test",
    collected_at=collected_at or datetime(2026, 9, 8, 8, 30, tzinfo=CHINA_TIMEZONE),
  )


def make_sector(code: str, score_offset: float) -> SectorSnapshot:
  """创建分数不同的板块快照。"""
  return SectorSnapshot(
    sector_code=code,
    sector_name=f"板块{code}",
    return_pct=1.0 + score_offset,
    breadth_pct=60 + score_offset,
    limit_up_count=3,
    turnover_change_pct=10,
    relative_strength_pct=1.0 + score_offset,
    persistence_days=2,
    catalyst_score=60,
    crowding_risk_score=30,
  )


def make_red_news(published_at: datetime) -> NewsEvent:
  """创建满足红色闸门的重大利空。"""
  return NewsEvent(
    event_id="risk-001",
    published_at=published_at,
    title="重大风险",
    event_type="宏观",
    sentiment=-1.0,
    impact_level=5,
    confidence=0.9,
    affected_sectors=(),
    source_count=2,
    is_major_risk=True,
  )


def test_pipeline_outputs_environment_position_risk_and_sectors() -> None:
  """有效输入必须产生完整、可执行的盘前结论。"""
  decision = build_pre_market_decision(
    report_date=REPORT_DATE,
    generated_at=datetime(2026, 9, 8, 8, 40, tzinfo=CHINA_TIMEZONE),
    cutoff_at=CUTOFF_AT,
    market_premium=make_market(),
    external_markets=(),
    news_events=(),
    sectors=(make_sector("A", 2), make_sector("B", 1), make_sector("C", 0), make_sector("D", -1)),
  )

  assert decision.status == "READY"
  assert decision.environment is not None
  assert decision.environment.environment is MarketEnvironment.PROFIT_EFFECT
  assert decision.position.final_grade is PermissionGrade.A
  assert decision.risk.level is RiskLevel.NONE
  assert len(decision.sector_outlooks) == 3


def test_pipeline_degrades_safely_for_core_data_after_cutoff() -> None:
  """截止时间后采集的核心数据不能产生交易许可。"""
  decision = build_pre_market_decision(
    report_date=REPORT_DATE,
    generated_at=datetime(2026, 9, 8, 9, 0, tzinfo=CHINA_TIMEZONE),
    cutoff_at=CUTOFF_AT,
    market_premium=make_market(
      collected_at=datetime(2026, 9, 8, 8, 51, tzinfo=CHINA_TIMEZONE),
    ),
    external_markets=(),
    news_events=(),
    sectors=(),
  )

  assert decision.status == "INSUFFICIENT_DATA"
  assert decision.environment is None
  assert decision.position.final_grade is PermissionGrade.D
  assert decision.position.total.maximum == 0
  assert "marketPremium.collectedAt" in decision.missing_fields


def test_news_after_cutoff_does_not_change_formal_decision() -> None:
  """截止时间后的重大利空不得反向改写正式报告。"""
  decision = build_pre_market_decision(
    report_date=REPORT_DATE,
    generated_at=datetime(2026, 9, 8, 9, 0, tzinfo=CHINA_TIMEZONE),
    cutoff_at=CUTOFF_AT,
    market_premium=make_market(),
    external_markets=(),
    news_events=(
      make_red_news(datetime(2026, 9, 8, 8, 51, tzinfo=CHINA_TIMEZONE)),
    ),
    sectors=(),
  )

  assert decision.risk.level is RiskLevel.NONE
  assert decision.position.final_grade is PermissionGrade.A
  assert "newsEvents.afterCutoff" in decision.missing_fields

