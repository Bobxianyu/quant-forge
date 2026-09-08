"""外围市场和新闻风险闸门测试。"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from quant_forge.domain.models import ExternalMarketSnapshot, NewsEvent, RiskLevel, SessionStatus
from quant_forge.rules.risk import assess_risk

CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


def make_news(
  *,
  sentiment: float = -1.0,
  impact_level: int = 5,
  confidence: float = 0.9,
  source_count: int = 2,
  is_major_risk: bool = True,
) -> NewsEvent:
  """创建新闻风险测试事件。"""
  return NewsEvent(
    event_id="news-001",
    published_at=datetime(2026, 9, 8, 7, 30, tzinfo=CHINA_TIMEZONE),
    title="测试风险事件",
    event_type="宏观",
    sentiment=sentiment,
    impact_level=impact_level,
    confidence=confidence,
    affected_sectors=(),
    source_count=source_count,
    is_major_risk=is_major_risk,
  )


def make_external(
  symbol: str,
  return_pct: float,
  session_status: SessionStatus = SessionStatus.CLOSED,
) -> ExternalMarketSnapshot:
  """创建外围市场测试快照。"""
  return ExternalMarketSnapshot(
    symbol=symbol,
    market_date=date(2026, 9, 7),
    return_pct=return_pct,
    session_status=session_status,
    source="test",
    collected_at=datetime(2026, 9, 8, 8, 30, tzinfo=CHINA_TIMEZONE),
  )


def test_red_risk_requires_major_high_confidence_negative_news() -> None:
  """高可信、多来源的五级重大利空触发红色风险。"""
  result = assess_risk([], [make_news()])

  assert result.level is RiskLevel.RED
  assert ("impactLevel", "5") in result.evidence[0].actual_values
  assert ("confidence", "0.90") in result.evidence[0].actual_values


def test_nasdaq_drop_at_three_percent_triggers_orange() -> None:
  """已收盘纳指跌幅达到负三时触发橙色风险。"""
  result = assess_risk([make_external("NASDAQ", -3.0)], [])

  assert result.level is RiskLevel.ORANGE


def test_dow_drop_at_three_percent_triggers_orange() -> None:
  """道指属于必须监控的美股指数，跌幅达到阈值时触发橙色风险。"""
  result = assess_risk([make_external("DOW", -3.0)], [])

  assert result.level is RiskLevel.ORANGE


def test_empty_external_markets_reports_all_expected_symbols_missing() -> None:
  """外围行情完全缺失时不得给出满可信度。"""
  result = assess_risk([], [])

  assert "externalMarkets.DOW" in result.missing_fields
  assert "externalMarkets.A50" in result.missing_fields


def test_partial_session_does_not_trigger_price_risk() -> None:
  """未完成时段的价格波动只能降低可信度，不能触发价格闸门。"""
  result = assess_risk([make_external("NASDAQ", -4.0, SessionStatus.PARTIAL)], [])

  assert result.level is RiskLevel.NONE
  assert "NASDAQ.sessionStatus" in result.missing_fields


def test_medium_confidence_negative_news_triggers_yellow() -> None:
  """三级且可信度达标的利空触发黄色风险。"""
  result = assess_risk(
    [],
    [make_news(impact_level=3, confidence=0.6, source_count=1, is_major_risk=False)],
  )

  assert result.level is RiskLevel.YELLOW


def test_positive_news_never_triggers_risk_gate() -> None:
  """正面新闻只进入正向因素，不触发风险闸门。"""
  result = assess_risk([], [make_news(sentiment=0.8)])

  assert result.level is RiskLevel.NONE
  assert result.positive_factors == ("测试风险事件",)
