"""板块相对强度评分、趋势判断和稳定排名。"""

from collections.abc import Iterable

from quant_forge.domain.models import RuleEvidence, SectorOutlook, SectorSnapshot, SectorTrend

MAX_RELATIVE_STRENGTH_PCT = 5.0
MIN_RELATIVE_STRENGTH_PCT = -5.0
MAX_LIMIT_UP_COUNT = 10
MAX_TURNOVER_CHANGE_PCT = 50.0
MIN_TURNOVER_CHANGE_PCT = -50.0
MAX_PERSISTENCE_DAYS = 5
CROWDED_RISK_THRESHOLD = 80.0
WEAK_BREADTH_THRESHOLD = 40.0
HEALTHY_BREADTH_THRESHOLD = 50.0
STRONG_CATALYST_THRESHOLD = 60.0


def rank_sectors(
  snapshots: Iterable[SectorSnapshot],
  *,
  limit: int = 3,
) -> tuple[SectorOutlook, ...]:
  """计算所有板块后按分数降序、代码升序返回指定数量。"""
  if limit < 0:
    raise ValueError("板块输出数量不能为负数")

  outlooks = tuple(_score_sector(snapshot) for snapshot in snapshots)
  ordered = sorted(outlooks, key=lambda item: (-item.score, item.sector_code))
  return tuple(ordered[:limit])


def _score_sector(snapshot: SectorSnapshot) -> SectorOutlook:
  """将原始量纲归一化为零至一百分后应用固定权重。"""
  relative_strength = _linear_score(
    snapshot.relative_strength_pct,
    MIN_RELATIVE_STRENGTH_PCT,
    MAX_RELATIVE_STRENGTH_PCT,
  )
  breadth = _clamp(snapshot.breadth_pct)
  limit_up = _linear_score(float(snapshot.limit_up_count), 0.0, float(MAX_LIMIT_UP_COUNT))
  turnover = _linear_score(
    snapshot.turnover_change_pct,
    MIN_TURNOVER_CHANGE_PCT,
    MAX_TURNOVER_CHANGE_PCT,
  )
  persistence = _linear_score(float(snapshot.persistence_days), 0.0, float(MAX_PERSISTENCE_DAYS))
  catalyst = _clamp(snapshot.catalyst_score)
  healthy_crowding = 100.0 - _clamp(snapshot.crowding_risk_score)

  score = round(
    relative_strength * 0.25
    + breadth * 0.15
    + limit_up * 0.15
    + turnover * 0.10
    + persistence * 0.10
    + catalyst * 0.15
    + healthy_crowding * 0.10,
    2,
  )
  trend, rule_id = _classify_trend(snapshot)
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
      ("healthyCrowding", f"{healthy_crowding:.2f}"),
    ),
    effect=f"板块分数={score:.2f}，趋势={trend.value}",
  )
  return SectorOutlook(
    sector_code=snapshot.sector_code,
    sector_name=snapshot.sector_name,
    score=score,
    trend=trend,
    evidence=(evidence,),
  )


def _classify_trend(snapshot: SectorSnapshot) -> tuple[SectorTrend, str]:
  """退潮风险优先，再判断延续、转强和观察。"""
  if snapshot.crowding_risk_score >= CROWDED_RISK_THRESHOLD or (
    snapshot.relative_strength_pct < 0 and snapshot.breadth_pct < WEAK_BREADTH_THRESHOLD
  ):
    return SectorTrend.POSSIBLE_EBB_TIDE, "SECTOR-EBB-001"
  if (
    snapshot.relative_strength_pct > 0
    and snapshot.breadth_pct >= HEALTHY_BREADTH_THRESHOLD
    and snapshot.turnover_change_pct >= 0
    and snapshot.persistence_days >= 2
  ):
    return SectorTrend.CONTINUATION, "SECTOR-CONTINUATION-001"
  if (
    snapshot.relative_strength_pct > 0
    and snapshot.catalyst_score >= STRONG_CATALYST_THRESHOLD
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
