"""盘前环境、风险、仓位和板块规则的统一编排。"""

from collections.abc import Iterable
from datetime import date, datetime

from quant_forge.domain.models import (
  ExternalMarketSnapshot,
  MarketPremiumSnapshot,
  NewsEvent,
  PermissionGrade,
  PreMarketDecision,
  RiskAssessment,
  RiskLevel,
  SectorSnapshot,
)
from quant_forge.rules.environment import assess_environment
from quant_forge.rules.position import decide_position
from quant_forge.rules.risk import assess_risk
from quant_forge.rules.sector import rank_sectors

READY_STATUS = "READY"
INSUFFICIENT_DATA_STATUS = "INSUFFICIENT_DATA"
DEFAULT_RULE_VERSION = "1.0.0"
DEFAULT_CONFIG_VERSION = "1.0.0"
DEFAULT_SECTOR_LIMIT = 3


def build_pre_market_decision(
  *,
  report_date: date,
  generated_at: datetime,
  cutoff_at: datetime,
  market_premium: MarketPremiumSnapshot,
  external_markets: Iterable[ExternalMarketSnapshot],
  news_events: Iterable[NewsEvent],
  sectors: Iterable[SectorSnapshot],
  rule_version: str = DEFAULT_RULE_VERSION,
  config_version: str = DEFAULT_CONFIG_VERSION,
) -> PreMarketDecision:
  """只使用截止时间前的数据生成一次可回放的盘前决策。"""
  _require_aware_datetime(generated_at, "generatedAt")
  _require_aware_datetime(cutoff_at, "cutoffAt")
  core_issues = _core_data_issues(report_date, cutoff_at, market_premium)
  if core_issues:
    return _insufficient_decision(
      report_date,
      generated_at,
      cutoff_at,
      market_premium,
      core_issues,
      rule_version,
      config_version,
    )

  usable_markets, market_issues = _markets_before_cutoff(external_markets, cutoff_at)
  usable_news, news_issues = _news_before_cutoff(news_events, cutoff_at)
  sector_snapshots = tuple(sectors)
  environment = assess_environment(market_premium)
  risk = assess_risk(usable_markets, usable_news)
  positive_adjustment = bool(risk.positive_factors) and not risk.negative_factors
  position = decide_position(
    environment.permission_grade,
    risk.level,
    positive_adjustment=positive_adjustment,
  )
  missing_fields = tuple(dict.fromkeys((*market_issues, *news_issues, *risk.missing_fields)))
  positive_factors = risk.positive_factors
  negative_factors = risk.negative_factors

  return PreMarketDecision(
    status=READY_STATUS,
    report_date=report_date,
    generated_at=generated_at,
    cutoff_at=cutoff_at,
    environment=environment,
    risk=risk,
    position=position,
    sector_outlooks=rank_sectors(sector_snapshots, limit=DEFAULT_SECTOR_LIMIT),
    confidence=_confidence(missing_fields),
    positive_factors=positive_factors,
    negative_factors=negative_factors,
    missing_fields=missing_fields,
    input_snapshot_ids=_snapshot_ids(market_premium, usable_markets, usable_news, sector_snapshots),
    rule_version=rule_version,
    config_version=config_version,
  )


def _core_data_issues(
  report_date: date,
  cutoff_at: datetime,
  snapshot: MarketPremiumSnapshot,
) -> tuple[str, ...]:
  """核心快照日期或采集时间异常时禁止产生交易许可。"""
  issues: list[str] = []
  try:
    _require_aware_datetime(snapshot.collected_at, "marketPremium.collectedAt")
  except ValueError:
    issues.append("marketPremium.collectedAt")
    return tuple(issues)
  if snapshot.collected_at > cutoff_at or snapshot.collected_at.date() != report_date:
    issues.append("marketPremium.collectedAt")
  if snapshot.trade_date >= report_date:
    issues.append("marketPremium.tradeDate")
  return tuple(issues)


def _markets_before_cutoff(
  markets: Iterable[ExternalMarketSnapshot],
  cutoff_at: datetime,
) -> tuple[tuple[ExternalMarketSnapshot, ...], tuple[str, ...]]:
  usable: list[ExternalMarketSnapshot] = []
  issues: list[str] = []
  for market in markets:
    try:
      _require_aware_datetime(market.collected_at, f"{market.symbol}.collectedAt")
    except ValueError:
      issues.append(f"{market.symbol}.collectedAt")
      continue
    if market.collected_at > cutoff_at:
      issues.append("externalMarkets.afterCutoff")
      continue
    usable.append(market)
  return tuple(usable), tuple(issues)


def _news_before_cutoff(
  events: Iterable[NewsEvent],
  cutoff_at: datetime,
) -> tuple[tuple[NewsEvent, ...], tuple[str, ...]]:
  usable: list[NewsEvent] = []
  issues: list[str] = []
  for event in events:
    try:
      _require_aware_datetime(event.published_at, f"{event.event_id}.publishedAt")
    except ValueError:
      issues.append(f"{event.event_id}.publishedAt")
      continue
    if event.published_at > cutoff_at:
      issues.append("newsEvents.afterCutoff")
      continue
    usable.append(event)
  return tuple(usable), tuple(issues)


def _insufficient_decision(
  report_date: date,
  generated_at: datetime,
  cutoff_at: datetime,
  snapshot: MarketPremiumSnapshot,
  issues: tuple[str, ...],
  rule_version: str,
  config_version: str,
) -> PreMarketDecision:
  """核心数据不可信时统一返回 D 级零仓位。"""
  risk = RiskAssessment(level=RiskLevel.NONE, missing_fields=issues)
  return PreMarketDecision(
    status=INSUFFICIENT_DATA_STATUS,
    report_date=report_date,
    generated_at=generated_at,
    cutoff_at=cutoff_at,
    environment=None,
    risk=risk,
    position=decide_position(PermissionGrade.D, RiskLevel.NONE),
    sector_outlooks=(),
    confidence=0.0,
    missing_fields=issues,
    input_snapshot_ids=(f"market:{snapshot.trade_date.isoformat()}:{snapshot.source}",),
    rule_version=rule_version,
    config_version=config_version,
  )


def _snapshot_ids(
  market: MarketPremiumSnapshot,
  external: tuple[ExternalMarketSnapshot, ...],
  news: tuple[NewsEvent, ...],
  sectors: tuple[SectorSnapshot, ...],
) -> tuple[str, ...]:
  """生成无需暴露敏感信息的稳定输入快照标识。"""
  return (
    f"market:{market.trade_date.isoformat()}:{market.source}",
    *(f"external:{item.symbol}:{item.market_date.isoformat()}" for item in external),
    *(f"news:{item.event_id}" for item in news),
    *(f"sector:{item.sector_code}:{market.trade_date.isoformat()}" for item in sectors),
  )


def _confidence(missing_fields: tuple[str, ...]) -> float:
  """每个非核心缺失项降低一成可信度，最低保留零分。"""
  return round(max(0.0, 1.0 - len(missing_fields) * 0.1), 2)


def _require_aware_datetime(value: datetime, field_name: str) -> None:
  """拒绝无法与盘前截止时间安全比较的无时区时间。"""
  if value.tzinfo is None or value.utcoffset() is None:
    raise ValueError(f"{field_name} 必须包含时区")

