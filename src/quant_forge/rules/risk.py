"""基于已完成外围行情和结构化新闻的风险闸门。"""

from collections.abc import Iterable

from quant_forge.domain.models import (
  ExternalMarketSnapshot,
  NewsEvent,
  RiskAssessment,
  RiskLevel,
  RuleEvidence,
  SessionStatus,
)

US_INDEX_SYMBOLS = frozenset({"SP500", "NASDAQ"})
A50_SYMBOL = "A50"
RED_NEWS_IMPACT_LEVEL = 5
RED_NEWS_CONFIDENCE = 0.80
RED_NEWS_SOURCE_COUNT = 2
ORANGE_NEWS_IMPACT_LEVEL = 4
ORANGE_NEWS_CONFIDENCE = 0.70
ORANGE_NEWS_SOURCE_COUNT = 2
YELLOW_NEWS_IMPACT_LEVEL = 3
YELLOW_NEWS_CONFIDENCE = 0.60
ORANGE_US_INDEX_DROP_PCT = -3.0
ORANGE_A50_DROP_PCT = -2.5
YELLOW_US_INDEX_DROP_PCT = -1.5
YELLOW_A50_DROP_PCT = -1.5


def assess_risk(
  external_markets: Iterable[ExternalMarketSnapshot],
  news_events: Iterable[NewsEvent],
) -> RiskAssessment:
  """按红、橙、黄顺序评估风险，并保留正负因素。"""
  markets = tuple(external_markets)
  news = tuple(news_events)
  usable_markets = tuple(item for item in markets if item.session_status is SessionStatus.CLOSED)
  missing_fields = tuple(
    f"{item.symbol}.sessionStatus"
    for item in markets
    if item.session_status is not SessionStatus.CLOSED
  )
  positive_factors = tuple(item.title for item in news if item.sentiment > 0 and item.confidence >= 0.60)
  negative_factors = tuple(item.title for item in news if item.sentiment < 0 and item.confidence >= 0.60)

  red_news = next((item for item in news if _is_red_news(item)), None)
  if red_news is not None:
    return _assessment(
      RiskLevel.RED,
      "RISK-RED-NEWS-001",
      red_news.title,
      positive_factors,
      negative_factors,
      missing_fields,
    )

  orange_news = next((item for item in news if _is_orange_news(item)), None)
  if orange_news is not None:
    return _assessment(
      RiskLevel.ORANGE,
      "RISK-ORANGE-NEWS-001",
      orange_news.title,
      positive_factors,
      negative_factors,
      missing_fields,
    )

  orange_market = next((item for item in usable_markets if _is_orange_market(item)), None)
  if orange_market is not None:
    return _market_assessment(
      RiskLevel.ORANGE,
      "RISK-ORANGE-MARKET-001",
      orange_market,
      positive_factors,
      negative_factors,
      missing_fields,
    )

  yellow_news = next((item for item in news if _is_yellow_news(item)), None)
  if yellow_news is not None:
    return _assessment(
      RiskLevel.YELLOW,
      "RISK-YELLOW-NEWS-001",
      yellow_news.title,
      positive_factors,
      negative_factors,
      missing_fields,
    )

  yellow_market = next((item for item in usable_markets if _is_yellow_market(item)), None)
  if yellow_market is not None:
    return _market_assessment(
      RiskLevel.YELLOW,
      "RISK-YELLOW-MARKET-001",
      yellow_market,
      positive_factors,
      negative_factors,
      missing_fields,
    )

  return RiskAssessment(
    level=RiskLevel.NONE,
    positive_factors=positive_factors,
    negative_factors=negative_factors,
    missing_fields=missing_fields,
  )


def _is_red_news(event: NewsEvent) -> bool:
  return (
    event.sentiment < 0
    and event.is_major_risk
    and event.impact_level >= RED_NEWS_IMPACT_LEVEL
    and event.confidence >= RED_NEWS_CONFIDENCE
    and event.source_count >= RED_NEWS_SOURCE_COUNT
  )


def _is_orange_news(event: NewsEvent) -> bool:
  return (
    event.sentiment < 0
    and event.impact_level >= ORANGE_NEWS_IMPACT_LEVEL
    and event.confidence >= ORANGE_NEWS_CONFIDENCE
    and event.source_count >= ORANGE_NEWS_SOURCE_COUNT
  )


def _is_yellow_news(event: NewsEvent) -> bool:
  return (
    event.sentiment < 0
    and event.impact_level >= YELLOW_NEWS_IMPACT_LEVEL
    and event.confidence >= YELLOW_NEWS_CONFIDENCE
  )


def _is_orange_market(market: ExternalMarketSnapshot) -> bool:
  symbol = market.symbol.upper()
  return (
    symbol in US_INDEX_SYMBOLS and market.return_pct <= ORANGE_US_INDEX_DROP_PCT
  ) or (symbol == A50_SYMBOL and market.return_pct <= ORANGE_A50_DROP_PCT)


def _is_yellow_market(market: ExternalMarketSnapshot) -> bool:
  symbol = market.symbol.upper()
  return (
    symbol in US_INDEX_SYMBOLS and market.return_pct <= YELLOW_US_INDEX_DROP_PCT
  ) or (symbol == A50_SYMBOL and market.return_pct <= YELLOW_A50_DROP_PCT)


def _assessment(
  level: RiskLevel,
  rule_id: str,
  title: str,
  positive_factors: tuple[str, ...],
  negative_factors: tuple[str, ...],
  missing_fields: tuple[str, ...],
) -> RiskAssessment:
  evidence = RuleEvidence(
    rule_id=rule_id,
    description="结构化新闻触发盘前风险闸门",
    actual_values=(("title", title),),
    effect=f"风险等级={level.value}",
  )
  return RiskAssessment(level, (evidence,), positive_factors, negative_factors, missing_fields)


def _market_assessment(
  level: RiskLevel,
  rule_id: str,
  market: ExternalMarketSnapshot,
  positive_factors: tuple[str, ...],
  negative_factors: tuple[str, ...],
  missing_fields: tuple[str, ...],
) -> RiskAssessment:
  evidence = RuleEvidence(
    rule_id=rule_id,
    description="已完成外围行情触发盘前风险闸门",
    actual_values=((market.symbol, f"{market.return_pct:.2f}%"),),
    effect=f"风险等级={level.value}",
  )
  return RiskAssessment(level, (evidence,), positive_factors, negative_factors, missing_fields)
