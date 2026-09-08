"""基于已完成外围行情和结构化新闻的风险闸门。"""

from collections.abc import Iterable

from quant_forge.config import RiskRuleConfig, load_default_config
from quant_forge.domain.models import (
  ExternalMarketSnapshot,
  NewsEvent,
  RiskAssessment,
  RiskLevel,
  RuleEvidence,
  SessionStatus,
)


def assess_risk(
  external_markets: Iterable[ExternalMarketSnapshot],
  news_events: Iterable[NewsEvent],
  config: RiskRuleConfig | None = None,
) -> RiskAssessment:
  """按红、橙、黄顺序评估风险，并保留正负因素。"""
  rules = config or load_default_config().risk
  markets = tuple(external_markets)
  news = tuple(news_events)
  usable_markets = tuple(item for item in markets if item.session_status is SessionStatus.CLOSED)
  present_symbols = frozenset(item.symbol.upper() for item in markets)
  missing_fields = (
    *(f"externalMarkets.{symbol}" for symbol in sorted(rules.expected_symbols - present_symbols)),
    *(
      f"{item.symbol}.sessionStatus"
      for item in markets
      if item.session_status is not SessionStatus.CLOSED
    ),
  )
  positive_factors = tuple(
    item.title for item in news if item.sentiment > 0 and item.confidence >= rules.yellow_news_confidence
  )
  negative_factors = tuple(
    item.title for item in news if item.sentiment < 0 and item.confidence >= rules.yellow_news_confidence
  )

  red_news = next((item for item in news if _is_red_news(item, rules)), None)
  if red_news is not None:
    return _assessment(
      RiskLevel.RED,
      "RISK-RED-NEWS-001",
      red_news,
      positive_factors,
      negative_factors,
      missing_fields,
      (
        ("sentiment", "< 0"),
        ("isMajorRisk", "true"),
        ("impactLevel", str(rules.red_news_impact_level)),
        ("confidence", f"{rules.red_news_confidence:.2f}"),
        ("sourceCount", str(rules.red_news_source_count)),
      ),
    )

  orange_news = next((item for item in news if _is_orange_news(item, rules)), None)
  if orange_news is not None:
    return _assessment(
      RiskLevel.ORANGE,
      "RISK-ORANGE-NEWS-001",
      orange_news,
      positive_factors,
      negative_factors,
      missing_fields,
      (
        ("sentiment", "< 0"),
        ("impactLevel", str(rules.orange_news_impact_level)),
        ("confidence", f"{rules.orange_news_confidence:.2f}"),
        ("sourceCount", str(rules.orange_news_source_count)),
      ),
    )

  orange_market = next((item for item in usable_markets if _is_orange_market(item, rules)), None)
  if orange_market is not None:
    return _market_assessment(
      RiskLevel.ORANGE,
      "RISK-ORANGE-MARKET-001",
      orange_market,
      positive_factors,
      negative_factors,
      missing_fields,
      rules,
    )

  yellow_news = next((item for item in news if _is_yellow_news(item, rules)), None)
  if yellow_news is not None:
    return _assessment(
      RiskLevel.YELLOW,
      "RISK-YELLOW-NEWS-001",
      yellow_news,
      positive_factors,
      negative_factors,
      missing_fields,
      (
        ("sentiment", "< 0"),
        ("impactLevel", str(rules.yellow_news_impact_level)),
        ("confidence", f"{rules.yellow_news_confidence:.2f}"),
      ),
    )

  yellow_market = next((item for item in usable_markets if _is_yellow_market(item, rules)), None)
  if yellow_market is not None:
    return _market_assessment(
      RiskLevel.YELLOW,
      "RISK-YELLOW-MARKET-001",
      yellow_market,
      positive_factors,
      negative_factors,
      missing_fields,
      rules,
    )

  return RiskAssessment(
    level=RiskLevel.NONE,
    positive_factors=positive_factors,
    negative_factors=negative_factors,
    missing_fields=missing_fields,
  )


def _is_red_news(event: NewsEvent, config: RiskRuleConfig) -> bool:
  return (
    event.sentiment < 0
    and event.is_major_risk
    and event.impact_level >= config.red_news_impact_level
    and event.confidence >= config.red_news_confidence
    and event.source_count >= config.red_news_source_count
  )


def _is_orange_news(event: NewsEvent, config: RiskRuleConfig) -> bool:
  return (
    event.sentiment < 0
    and event.impact_level >= config.orange_news_impact_level
    and event.confidence >= config.orange_news_confidence
    and event.source_count >= config.orange_news_source_count
  )


def _is_yellow_news(event: NewsEvent, config: RiskRuleConfig) -> bool:
  return (
    event.sentiment < 0
    and event.impact_level >= config.yellow_news_impact_level
    and event.confidence >= config.yellow_news_confidence
  )


def _is_orange_market(market: ExternalMarketSnapshot, config: RiskRuleConfig) -> bool:
  symbol = market.symbol.upper()
  return (
    symbol in config.us_index_symbols and market.return_pct <= config.orange_us_index_drop_pct
  ) or (symbol in config.a50_symbols and market.return_pct <= config.orange_a50_drop_pct)


def _is_yellow_market(market: ExternalMarketSnapshot, config: RiskRuleConfig) -> bool:
  symbol = market.symbol.upper()
  return (
    symbol in config.us_index_symbols and market.return_pct <= config.yellow_us_index_drop_pct
  ) or (symbol in config.a50_symbols and market.return_pct <= config.yellow_a50_drop_pct)


def _assessment(
  level: RiskLevel,
  rule_id: str,
  event: NewsEvent,
  positive_factors: tuple[str, ...],
  negative_factors: tuple[str, ...],
  missing_fields: tuple[str, ...],
  thresholds: tuple[tuple[str, str], ...],
) -> RiskAssessment:
  evidence = RuleEvidence(
    rule_id=rule_id,
    description="结构化新闻触发盘前风险闸门",
    actual_values=(
      ("title", event.title),
      ("impactLevel", str(event.impact_level)),
      ("confidence", f"{event.confidence:.2f}"),
      ("sourceCount", str(event.source_count)),
      ("isMajorRisk", str(event.is_major_risk).lower()),
    ),
    effect=f"风险等级={level.value}",
    thresholds=thresholds,
  )
  return RiskAssessment(level, (evidence,), positive_factors, negative_factors, missing_fields)


def _market_assessment(
  level: RiskLevel,
  rule_id: str,
  market: ExternalMarketSnapshot,
  positive_factors: tuple[str, ...],
  negative_factors: tuple[str, ...],
  missing_fields: tuple[str, ...],
  config: RiskRuleConfig,
) -> RiskAssessment:
  evidence = RuleEvidence(
    rule_id=rule_id,
    description="已完成外围行情触发盘前风险闸门",
    actual_values=((market.symbol, f"{market.return_pct:.2f}%"),),
    effect=f"风险等级={level.value}",
    thresholds=(("triggerReturnPct", f"{_market_threshold(level, market, config):.2f}%"),),
  )
  return RiskAssessment(level, (evidence,), positive_factors, negative_factors, missing_fields)


def _market_threshold(
  level: RiskLevel,
  market: ExternalMarketSnapshot,
  config: RiskRuleConfig,
) -> float:
  """返回当前标的和风险级别对应的跌幅阈值。"""
  is_us_index = market.symbol.upper() in config.us_index_symbols
  if level is RiskLevel.ORANGE:
    return config.orange_us_index_drop_pct if is_us_index else config.orange_a50_drop_pct
  return config.yellow_us_index_drop_pct if is_us_index else config.yellow_a50_drop_pct
