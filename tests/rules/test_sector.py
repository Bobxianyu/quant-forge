"""板块评分、趋势和排名测试。"""

from quant_forge.domain.models import SectorSnapshot, SectorTrend
from quant_forge.rules.sector import rank_sectors


def make_sector(
  code: str,
  *,
  relative_strength: float,
  breadth: float,
  limit_up_count: int,
  turnover_change: float,
  persistence_days: int,
  catalyst_score: float,
  crowding_score: float,
) -> SectorSnapshot:
  """创建板块测试快照。"""
  return SectorSnapshot(
    sector_code=code,
    sector_name=f"板块{code}",
    return_pct=relative_strength,
    breadth_pct=breadth,
    limit_up_count=limit_up_count,
    turnover_change_pct=turnover_change,
    relative_strength_pct=relative_strength,
    persistence_days=persistence_days,
    catalyst_score=catalyst_score,
    crowding_risk_score=crowding_score,
  )


def test_stronger_sector_ranks_first() -> None:
  """内部强度和催化更好的板块必须排在弱板块前面。"""
  strong = make_sector(
    "A",
    relative_strength=2.0,
    breadth=70,
    limit_up_count=5,
    turnover_change=20,
    persistence_days=3,
    catalyst_score=70,
    crowding_score=30,
  )
  weak = make_sector(
    "B",
    relative_strength=-1.0,
    breadth=40,
    limit_up_count=1,
    turnover_change=-10,
    persistence_days=1,
    catalyst_score=20,
    crowding_score=50,
  )

  result = rank_sectors([weak, strong], limit=3)

  assert result[0].sector_code == "A"
  assert result[0].trend is SectorTrend.CONTINUATION
  assert result[0].score > result[1].score


def test_crowded_sector_with_weak_breadth_is_possible_ebb_tide() -> None:
  """拥挤度高且内部广度弱时优先标记可能退潮。"""
  crowded = make_sector(
    "C",
    relative_strength=1.0,
    breadth=30,
    limit_up_count=2,
    turnover_change=-5,
    persistence_days=4,
    catalyst_score=30,
    crowding_score=90,
  )

  result = rank_sectors([crowded], limit=3)

  assert result[0].trend is SectorTrend.POSSIBLE_EBB_TIDE


def test_new_catalyst_with_positive_strength_is_possible_strengthening() -> None:
  """新增催化和相对强度改善可判为可能转强。"""
  sector = make_sector(
    "D",
    relative_strength=0.5,
    breadth=55,
    limit_up_count=2,
    turnover_change=-5,
    persistence_days=1,
    catalyst_score=80,
    crowding_score=20,
  )

  result = rank_sectors([sector], limit=3)

  assert result[0].trend is SectorTrend.POSSIBLE_STRENGTHENING


def test_equal_scores_are_sorted_by_sector_code() -> None:
  """同分板块按代码排序，保证历史回放结果稳定。"""
  first = make_sector(
    "A",
    relative_strength=0,
    breadth=50,
    limit_up_count=1,
    turnover_change=0,
    persistence_days=1,
    catalyst_score=50,
    crowding_score=50,
  )
  second = make_sector(
    "B",
    relative_strength=0,
    breadth=50,
    limit_up_count=1,
    turnover_change=0,
    persistence_days=1,
    catalyst_score=50,
    crowding_score=50,
  )

  result = rank_sectors([second, first], limit=3)

  assert [item.sector_code for item in result] == ["A", "B"]


def test_limit_rejects_negative_values() -> None:
  """负数排名数量属于非法输入。"""
  try:
    rank_sectors([], limit=-1)
  except ValueError as error:
    assert str(error) == "板块输出数量不能为负数"
  else:
    raise AssertionError("负数排名数量必须抛出异常")
