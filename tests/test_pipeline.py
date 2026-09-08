"""完整盘前决策管道测试。"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from quant_forge.domain.models import (
  ExternalMarketSnapshot,
  MarketEnvironment,
  MarketPremiumSnapshot,
  NewsEvent,
  PermissionGrade,
  RiskLevel,
  SectorSnapshot,
  SessionStatus,
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
    trade_date=date(2026, 9, 7),
    collected_at=datetime(2026, 9, 8, 8, 30, tzinfo=CHINA_TIMEZONE),
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
    expected_market_date=date(2026, 9, 7),
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
    expected_market_date=date(2026, 9, 7),
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
    expected_market_date=date(2026, 9, 7),
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
  assert "newsEvents.afterAsOf" in decision.missing_fields


def test_non_finite_core_metric_forces_zero_position() -> None:
  """NaN 等非有限核心值不得进入任何交易许可判断。"""
  invalid_market = MarketPremiumSnapshot(
    trade_date=date(2026, 9, 7),
    first_board_premium_pct=float("nan"),
    second_board_premium_pct=1.0,
    multi_board_premium_pct=0.5,
    limit_up_premium_pct=2.2,
    source="test",
    collected_at=datetime(2026, 9, 8, 8, 30, tzinfo=CHINA_TIMEZONE),
  )

  decision = build_pre_market_decision(
    report_date=REPORT_DATE,
    expected_market_date=date(2026, 9, 7),
    generated_at=datetime(2026, 9, 8, 8, 40, tzinfo=CHINA_TIMEZONE),
    cutoff_at=CUTOFF_AT,
    market_premium=invalid_market,
    external_markets=(),
    news_events=(),
    sectors=(),
  )

  assert decision.status == "INSUFFICIENT_DATA"
  assert decision.position.final_grade is PermissionGrade.D
  assert decision.position.total.maximum == 0
  assert "marketPremium.firstBoardPremiumPct" in decision.missing_fields


def test_snapshot_must_match_explicit_previous_trade_date() -> None:
  """显式上一交易日可正确覆盖周末和中国节假日。"""
  old_market = MarketPremiumSnapshot(
    trade_date=date(2026, 8, 1),
    first_board_premium_pct=2.5,
    second_board_premium_pct=1.0,
    multi_board_premium_pct=0.5,
    limit_up_premium_pct=2.2,
    source="test",
    collected_at=datetime(2026, 9, 8, 8, 30, tzinfo=CHINA_TIMEZONE),
  )

  decision = build_pre_market_decision(
    report_date=REPORT_DATE,
    expected_market_date=date(2026, 9, 7),
    generated_at=datetime(2026, 9, 8, 8, 40, tzinfo=CHINA_TIMEZONE),
    cutoff_at=CUTOFF_AT,
    market_premium=old_market,
    external_markets=(),
    news_events=(),
    sectors=(),
  )

  assert decision.status == "INSUFFICIENT_DATA"
  assert "marketPremium.tradeDate" in decision.missing_fields


def test_data_after_generation_time_is_excluded() -> None:
  """即使数据早于 08:50，也不能使用晚于本次实际生成时间的数据。"""
  decision = build_pre_market_decision(
    report_date=REPORT_DATE,
    expected_market_date=date(2026, 9, 7),
    generated_at=datetime(2026, 9, 8, 8, 40, tzinfo=CHINA_TIMEZONE),
    cutoff_at=CUTOFF_AT,
    market_premium=make_market(
      collected_at=datetime(2026, 9, 8, 8, 45, tzinfo=CHINA_TIMEZONE),
    ),
    external_markets=(),
    news_events=(),
    sectors=(),
  )

  assert decision.status == "INSUFFICIENT_DATA"
  assert decision.position.total.maximum == 0


def test_non_formal_cutoff_time_forces_zero_position() -> None:
  """调用方不能把正式报告截止时间改到盘后。"""
  decision = build_pre_market_decision(
    report_date=REPORT_DATE,
    expected_market_date=date(2026, 9, 7),
    generated_at=datetime(2026, 9, 8, 8, 40, tzinfo=CHINA_TIMEZONE),
    cutoff_at=datetime(2026, 9, 8, 15, 0, tzinfo=CHINA_TIMEZONE),
    market_premium=make_market(),
    external_markets=(),
    news_events=(),
    sectors=(),
  )

  assert decision.status == "INSUFFICIENT_DATA"
  assert decision.position.total.maximum == 0
  assert "cutoffAt" in decision.missing_fields


def test_future_sector_snapshot_is_excluded() -> None:
  """板块数据晚于实际生成时刻时不得进入排名。"""
  future = make_sector("FUTURE", 2)
  future = SectorSnapshot(
    sector_code=future.sector_code,
    sector_name=future.sector_name,
    return_pct=future.return_pct,
    breadth_pct=future.breadth_pct,
    limit_up_count=future.limit_up_count,
    turnover_change_pct=future.turnover_change_pct,
    relative_strength_pct=future.relative_strength_pct,
    persistence_days=future.persistence_days,
    catalyst_score=future.catalyst_score,
    crowding_risk_score=future.crowding_risk_score,
    trade_date=future.trade_date,
    collected_at=datetime(2026, 9, 8, 8, 45, tzinfo=CHINA_TIMEZONE),
  )
  decision = build_pre_market_decision(
    report_date=REPORT_DATE,
    expected_market_date=date(2026, 9, 7),
    generated_at=datetime(2026, 9, 8, 8, 40, tzinfo=CHINA_TIMEZONE),
    cutoff_at=CUTOFF_AT,
    market_premium=make_market(),
    external_markets=(),
    news_events=(),
    sectors=(future,),
  )

  assert decision.sector_outlooks == ()
  assert "sectors.afterAsOf" in decision.missing_fields


def test_previous_trade_date_must_be_before_report_date() -> None:
  """误把报告日当作上一交易日时必须输出 D 级零仓位。"""
  same_day_market = MarketPremiumSnapshot(
    trade_date=REPORT_DATE,
    first_board_premium_pct=2.5,
    second_board_premium_pct=1.0,
    multi_board_premium_pct=0.5,
    limit_up_premium_pct=2.2,
    source="test",
    collected_at=datetime(2026, 9, 8, 8, 30, tzinfo=CHINA_TIMEZONE),
  )
  decision = build_pre_market_decision(
    report_date=REPORT_DATE,
    expected_market_date=REPORT_DATE,
    generated_at=datetime(2026, 9, 8, 8, 50, tzinfo=CHINA_TIMEZONE),
    cutoff_at=CUTOFF_AT,
    market_premium=same_day_market,
    external_markets=(),
    news_events=(),
    sectors=(),
  )

  assert decision.status == "INSUFFICIENT_DATA"
  assert decision.position.total.maximum == 0
  assert "expectedMarketDate" in decision.missing_fields


def test_news_at_cutoff_is_included() -> None:
  """规格允许恰好在 08:50 发布的新闻进入正式报告。"""
  decision = build_pre_market_decision(
    report_date=REPORT_DATE,
    expected_market_date=date(2026, 9, 7),
    generated_at=CUTOFF_AT,
    cutoff_at=CUTOFF_AT,
    market_premium=make_market(),
    external_markets=(),
    news_events=(make_red_news(CUTOFF_AT),),
    sectors=(),
  )

  assert decision.risk.level is RiskLevel.RED
  assert decision.position.total.maximum == 0


def test_external_market_at_cutoff_is_included() -> None:
  """恰好在 08:50 采集的已收盘外围行情可进入风险闸门。"""
  dow = ExternalMarketSnapshot(
    symbol="DOW",
    market_date=date(2026, 9, 7),
    return_pct=-3.0,
    session_status=SessionStatus.CLOSED,
    source="test",
    collected_at=CUTOFF_AT,
  )
  decision = build_pre_market_decision(
    report_date=REPORT_DATE,
    expected_market_date=date(2026, 9, 7),
    generated_at=CUTOFF_AT,
    cutoff_at=CUTOFF_AT,
    market_premium=make_market(),
    external_markets=(dow,),
    news_events=(),
    sectors=(),
  )

  assert decision.risk.level is RiskLevel.ORANGE
  assert decision.position.final_grade is PermissionGrade.C


def test_previous_evening_core_snapshot_is_accepted() -> None:
  """上一交易日晚间形成的完整 T-1 快照可用于次日盘前判断。"""
  decision = build_pre_market_decision(
    report_date=REPORT_DATE,
    expected_market_date=date(2026, 9, 7),
    generated_at=datetime(2026, 9, 8, 8, 40, tzinfo=CHINA_TIMEZONE),
    cutoff_at=CUTOFF_AT,
    market_premium=make_market(
      collected_at=datetime(2026, 9, 7, 18, 0, tzinfo=CHINA_TIMEZONE),
    ),
    external_markets=(),
    news_events=(),
    sectors=(),
  )

  assert decision.status == "READY"
  assert decision.config_snapshot_id.startswith("config:sha256:")
