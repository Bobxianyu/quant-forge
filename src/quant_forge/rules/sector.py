"""板块相对强度评分、趋势判断和稳定排名。"""

from collections.abc import Iterable

from quant_forge.config import SectorRuleConfig, load_default_config
from quant_forge.domain.models import RuleEvidence, SectorOutlook, SectorSnapshot, SectorTrend


def rank_sectors(
  snapshots: Iterable[SectorSnapshot],
  *,
  limit: int | None = None,
  config: SectorRuleConfig | None = None,
) -> tuple[SectorOutlook, ...]:
  """计算所有板块后按分数降序、代码升序返回指定数量。"""
  rules = config or load_default_config().sector
  result_limit = rules.output_limit if limit is None else limit
  if result_limit < 0:
    raise ValueError("板块输出数量不能为负数")

  outlooks = tuple(_score_sector(snapshot, rules) for snapshot in snapshots)
  ordered = sorted(outlooks, key=lambda item: (-item.score, item.sector_code))
  return tuple(ordered[:result_limit])


def _score_sector(snapshot: SectorSnapshot, config: SectorRuleConfig) -> SectorOutlook:
  """将原始量纲归一化为零至一百分后应用固定权重。"""
  relative_strength = _linear_score(
    snapshot.relative_strength_pct,
    config.relative_strength_min_pct,
    config.relative_strength_max_pct,
  )
  breadth = _clamp(snapshot.breadth_pct)
  limit_up = _linear_score(float(snapshot.limit_up_count), 0.0, float(config.max_limit_up_count))
  turnover = _linear_score(
    snapshot.turnover_change_pct,
    config.turnover_change_min_pct,
    config.turnover_change_max_pct,
  )
  persistence = _linear_score(float(snapshot.persistence_days), 0.0, float(config.max_persistence_days))
  catalyst = _clamp(snapshot.catalyst_score)
  crowding_adjustment = config.crowding_max_adjustment * (
    1.0 - 2.0 * _clamp(snapshot.crowding_risk_score) / 100.0
  )

  score = round(
    _clamp(
      relative_strength * config.relative_strength_weight
      + breadth * config.breadth_weight
      + limit_up * config.limit_up_weight
      + turnover * config.turnover_weight
      + persistence * config.persistence_weight
      + catalyst * config.catalyst_weight
      + crowding_adjustment,
    ),
    2,
  )
  trend, rule_id = _classify_trend(snapshot, config)
  evidence = RuleEvidence(
    rule_id=rule_id,
    description="板块强度、广度、成交、持续性、催化和拥挤度加权评分",
    actual_values=(
      ("relativeStrength", f"{relative_strength:.2f}"),
      ("breadth", f"{breadth:.2f}"),
      ("limitUp", f"{limit_up:.2f}"),
      ("turnover", f"{turnover:.2f}"),
      ("persistence", f"{persistence:.2f}"),
      ("catalyst", f"{catalyst:.2f}"),
      ("crowdingAdjustment", f"{crowding_adjustment:.2f}"),
    ),
    effect=f"板块分数={score:.2f}，趋势={trend.value}",
    thresholds=(
      ("crowdedRiskThreshold", f"{config.crowded_risk_threshold:.2f}"),
      ("healthyBreadthThreshold", f"{config.healthy_breadth_threshold:.2f}"),
      ("strongCatalystThreshold", f"{config.strong_catalyst_threshold:.2f}"),
    ),
  )
  return SectorOutlook(
    sector_code=snapshot.sector_code,
    sector_name=snapshot.sector_name,
    score=score,
    trend=trend,
    evidence=(evidence,),
  )


def _classify_trend(snapshot: SectorSnapshot, config: SectorRuleConfig) -> tuple[SectorTrend, str]:
  """退潮风险优先，再判断延续、转强和观察。"""
  if snapshot.crowding_risk_score >= config.crowded_risk_threshold or (
    snapshot.relative_strength_pct < 0 and snapshot.breadth_pct < config.weak_breadth_threshold
  ):
    return SectorTrend.POSSIBLE_EBB_TIDE, "SECTOR-EBB-001"
  if (
    snapshot.relative_strength_pct > 0
    and snapshot.breadth_pct >= config.healthy_breadth_threshold
    and snapshot.turnover_change_pct >= 0
    and snapshot.persistence_days >= config.continuation_persistence_days
  ):
    return SectorTrend.CONTINUATION, "SECTOR-CONTINUATION-001"
  if (
    snapshot.relative_strength_pct > 0
    and snapshot.catalyst_score >= config.strong_catalyst_threshold
  ):
    return SectorTrend.POSSIBLE_STRENGTHENING, "SECTOR-STRENGTHENING-001"
  return SectorTrend.WATCH, "SECTOR-WATCH-001"


def _linear_score(value: float, minimum: float, maximum: float) -> float:
  """把指定区间线性映射到零至一百分，并截断越界值。"""
  clamped = min(max(value, minimum), maximum)
  return (clamped - minimum) / (maximum - minimum) * 100.0


def _clamp(value: float) -> float:
  """把百分制字段限制在零至一百。"""
  return min(max(value, 0.0), 100.0)
