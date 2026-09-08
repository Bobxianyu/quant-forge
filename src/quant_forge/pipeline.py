"""盘前环境、风险、仓位和板块规则的统一编排。"""

import hashlib
import math
from collections.abc import Iterable
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from quant_forge.config import (
  DecisionConfig,
  config_snapshot_id,
  load_default_config,
  validate_decision_config,
)
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
FORMAL_CUTOFF_TIME = time(8, 50)
CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


def build_pre_market_decision(
  *,
  report_date: date,
  expected_market_date: date,
  generated_at: datetime,
  cutoff_at: datetime,
  market_premium: MarketPremiumSnapshot,
  external_markets: Iterable[ExternalMarketSnapshot],
  news_events: Iterable[NewsEvent],
  sectors: Iterable[SectorSnapshot],
  config: DecisionConfig | None = None,
  input_issues: tuple[str, ...] = (),
) -> PreMarketDecision:
  """只使用实际生成时刻和正式截止时间之前的数据生成盘前决策。"""
  rules = config or load_default_config()
  validate_decision_config(rules)
  if expected_market_date >= report_date:
    return build_insufficient_decision(
      report_date=report_date,
      generated_at=generated_at,
      cutoff_at=cutoff_at,
      issues=tuple(dict.fromkeys((*input_issues, "expectedMarketDate"))),
      config=rules,
    )
  cutoff_issues = _cutoff_issues(report_date, cutoff_at)
  try:
    _require_aware_datetime(generated_at, "generatedAt")
  except ValueError:
    cutoff_issues = (*cutoff_issues, "generatedAt")
  if cutoff_issues:
    return build_insufficient_decision(
      report_date=report_date,
      generated_at=generated_at,
      cutoff_at=cutoff_at,
      issues=tuple(dict.fromkeys((*input_issues, *cutoff_issues))),
      config=rules,
    )

  as_of = min(generated_at, cutoff_at)
  core_issues = _core_data_issues(
    expected_market_date,
    as_of,
    cutoff_at,
    market_premium,
  )
  if core_issues:
    return build_insufficient_decision(
      report_date=report_date,
      generated_at=generated_at,
      cutoff_at=cutoff_at,
      issues=tuple(dict.fromkeys((*input_issues, *core_issues))),
      config=rules,
      market_premium=market_premium,
    )

  usable_markets, market_issues = _markets_before_as_of(
    external_markets,
    expected_market_date,
    report_date,
    as_of,
    cutoff_at,
  )
  usable_news, news_issues = _news_before_as_of(news_events, as_of, cutoff_at)
  usable_sectors, sector_issues = _sectors_before_as_of(
    sectors,
    expected_market_date,
    as_of,
    cutoff_at,
  )
  environment = assess_environment(market_premium, rules.environment)
  risk = assess_risk(usable_markets, usable_news, rules.risk)
  positive_adjustment = bool(risk.positive_factors) and not risk.negative_factors
  position = decide_position(
    environment.permission_grade,
    risk.level,
    rules.positions,
    positive_adjustment=positive_adjustment,
  )
  missing_fields = tuple(
    dict.fromkeys(
      (*input_issues, *market_issues, *news_issues, *sector_issues, *risk.missing_fields),
    ),
  )

  return PreMarketDecision(
    status=READY_STATUS,
    report_date=report_date,
    generated_at=generated_at,
    cutoff_at=cutoff_at,
    environment=environment,
    risk=risk,
    position=position,
    sector_outlooks=rank_sectors(usable_sectors, config=rules.sector),
    confidence=_confidence(missing_fields),
    positive_factors=risk.positive_factors,
    negative_factors=risk.negative_factors,
    missing_fields=missing_fields,
    input_snapshot_ids=_snapshot_ids(market_premium, usable_markets, usable_news, usable_sectors),
    rule_version=DEFAULT_RULE_VERSION,
    config_version=rules.version,
    config_snapshot_id=config_snapshot_id(rules),
  )


def build_insufficient_decision(
  *,
  report_date: date,
  generated_at: datetime,
  cutoff_at: datetime,
  issues: tuple[str, ...],
  config: DecisionConfig,
  market_premium: MarketPremiumSnapshot | None = None,
) -> PreMarketDecision:
  """配置已知但核心数据不可信时生成 D 级零仓位安全报告。"""
  validate_decision_config(config)
  unique_issues = tuple(dict.fromkeys(issues))
  snapshot_ids = () if market_premium is None else (_snapshot_id("market", market_premium),)
  risk = RiskAssessment(level=RiskLevel.NONE, missing_fields=unique_issues)
  return PreMarketDecision(
    status=INSUFFICIENT_DATA_STATUS,
    report_date=report_date,
    generated_at=generated_at,
    cutoff_at=cutoff_at,
    environment=None,
    risk=risk,
    position=decide_position(PermissionGrade.D, RiskLevel.NONE, config.positions),
    sector_outlooks=(),
    confidence=0.0,
    missing_fields=unique_issues,
    input_snapshot_ids=snapshot_ids,
    rule_version=DEFAULT_RULE_VERSION,
    config_version=config.version,
    config_snapshot_id=config_snapshot_id(config),
  )


def _cutoff_issues(report_date: date, cutoff_at: datetime) -> tuple[str, ...]:
  """正式截止时间固定为报告日北京时间 08:50。"""
  try:
    _require_aware_datetime(cutoff_at, "cutoffAt")
  except ValueError:
    return ("cutoffAt",)
  china_cutoff = cutoff_at.astimezone(CHINA_TIMEZONE)
  if china_cutoff.date() != report_date or china_cutoff.time().replace(tzinfo=None) != FORMAL_CUTOFF_TIME:
    return ("cutoffAt",)
  return ()


def _core_data_issues(
  expected_market_date: date,
  as_of: datetime,
  cutoff_at: datetime,
  snapshot: MarketPremiumSnapshot,
) -> tuple[str, ...]:
  """核心快照时间、交易日和有限数异常时禁止产生交易许可。"""
  issues: list[str] = []
  try:
    _require_aware_datetime(snapshot.collected_at, "marketPremium.collectedAt")
  except ValueError:
    issues.append("marketPremium.collectedAt")
    return tuple(issues)
  if snapshot.collected_at > as_of or snapshot.collected_at >= cutoff_at:
    issues.append("marketPremium.collectedAt")
  if snapshot.trade_date != expected_market_date:
    issues.append("marketPremium.tradeDate")
  finite_fields = (
    ("firstBoardPremiumPct", snapshot.first_board_premium_pct),
    ("secondBoardPremiumPct", snapshot.second_board_premium_pct),
    ("multiBoardPremiumPct", snapshot.multi_board_premium_pct),
    ("limitUpPremiumPct", snapshot.limit_up_premium_pct),
  )
  issues.extend(
    f"marketPremium.{field_name}"
    for field_name, value in finite_fields
    if not math.isfinite(value)
  )
  return tuple(dict.fromkeys(issues))


def _markets_before_as_of(
  markets: Iterable[ExternalMarketSnapshot],
  expected_market_date: date,
  report_date: date,
  as_of: datetime,
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
    if market.collected_at > as_of:
      issues.append("externalMarkets.afterAsOf")
      continue
    if market.market_date < expected_market_date or market.market_date > report_date:
      issues.append(f"{market.symbol}.marketDate")
      continue
    if not math.isfinite(market.return_pct):
      issues.append(f"{market.symbol}.returnPct")
      continue
    usable.append(market)
  return tuple(usable), tuple(issues)


def _news_before_as_of(
  events: Iterable[NewsEvent],
  as_of: datetime,
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
    if event.published_at > as_of:
      issues.append("newsEvents.afterAsOf")
      continue
    if not math.isfinite(event.sentiment) or not math.isfinite(event.confidence):
      issues.append(f"{event.event_id}.nonFinite")
      continue
    usable.append(event)
  return tuple(usable), tuple(issues)


def _sectors_before_as_of(
  sectors: Iterable[SectorSnapshot],
  expected_market_date: date,
  as_of: datetime,
  cutoff_at: datetime,
) -> tuple[tuple[SectorSnapshot, ...], tuple[str, ...]]:
  usable: list[SectorSnapshot] = []
  issues: list[str] = []
  for sector in sectors:
    if sector.trade_date != expected_market_date:
      issues.append(f"{sector.sector_code}.tradeDate")
      continue
    if sector.collected_at is None:
      issues.append(f"{sector.sector_code}.collectedAt")
      continue
    try:
      _require_aware_datetime(sector.collected_at, f"{sector.sector_code}.collectedAt")
    except ValueError:
      issues.append(f"{sector.sector_code}.collectedAt")
      continue
    if sector.collected_at > as_of or sector.collected_at >= cutoff_at:
      issues.append("sectors.afterAsOf")
      continue
    values = (
      sector.return_pct,
      sector.breadth_pct,
      sector.turnover_change_pct,
      sector.relative_strength_pct,
      sector.catalyst_score,
      sector.crowding_risk_score,
    )
    if not all(math.isfinite(value) for value in values):
      issues.append(f"{sector.sector_code}.nonFinite")
      continue
    usable.append(sector)
  return tuple(usable), tuple(issues)


def _snapshot_ids(
  market: MarketPremiumSnapshot,
  external: tuple[ExternalMarketSnapshot, ...],
  news: tuple[NewsEvent, ...],
  sectors: tuple[SectorSnapshot, ...],
) -> tuple[str, ...]:
  """使用内容摘要标识真实输入，避免同名数据无法区分。"""
  return (
    _snapshot_id("market", market),
    *(_snapshot_id(f"external:{item.symbol}", item) for item in external),
    *(_snapshot_id(f"news:{item.event_id}", item) for item in news),
    *(_snapshot_id(f"sector:{item.sector_code}", item) for item in sectors),
  )


def _snapshot_id(prefix: str, value: object) -> str:
  digest = hashlib.sha256(repr(value).encode("utf-8")).hexdigest()
  return f"{prefix}:sha256:{digest}"


def _confidence(missing_fields: tuple[str, ...]) -> float:
  """每个非核心缺失项降低一成可信度，最低为零。"""
  return round(max(0.0, 1.0 - len(missing_fields) * 0.1), 2)


def _require_aware_datetime(value: datetime, field_name: str) -> None:
  """拒绝无法与盘前截止时间安全比较的无时区时间。"""
  if value.tzinfo is None or value.utcoffset() is None:
    raise ValueError(f"{field_name} 必须包含时区")
