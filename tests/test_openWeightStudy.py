"""取消风险权重下限时，网格、风险门槛及冻结缓存必须保持明确边界。"""

from copy import deepcopy

import pytest
from test_observationStudy import daysFixture

from quant_forge.research import openWeightStudy as study
from quant_forge.research.observationStudy import weightGrid
from quant_forge.research.sealHitStudy import selectSealModel


def allSealedDays():
  """为网格与缓存测试构造事件充分的合成数据。"""
  days = daysFixture()
  for item in days.values():
    for row in item["rows"]:
      row["outcome"]["sealEvent"] = True
  return days


def testCompleteGridHas35UniqueCombinationsAndContainsOldGrid():
  grid = study.openWeightGrid()
  assert len(grid) == len(set(grid)) == 35
  assert set(weightGrid()) < set(grid)
  assert all(sum(weights) == 1 and all(w in (0, 0.25, 0.5, 0.75, 1) for w in weights) for weights in grid)
  assert (0.25, 0.25, 0.25, 0.25) in grid
  for index in range(4):
    assert tuple(float(i == index) for i in range(4)) in grid
  assert len([w for w in grid if w[0] < 0.5]) == 25


def test105CombinationsRetainRiskAndSealGates():
  result = study.selectOpenWeightModel(allSealedDays())
  assert len(result["comparisons"]) == 105
  assert sum(r["isEqualWeight"] for r in result["comparisons"]) == 3
  assert result["selected"] is not None
  for row in result["comparisons"]:
    assert row["eligible"] == all(not row[p]["failures"] for p in ("train", "validation"))
  assert result["oldHoldoutUsed"] is False and result["freshHoldoutStatus"] == "not_evaluated"


def testCached30AreReusedAndSameAsFreshComputation(monkeypatch):
  days = allSealedDays()
  prior = selectSealModel(days)
  original = deepcopy(prior)
  fresh = study.selectOpenWeightModel(days)
  evaluate = study.evaluateSealModel
  calls = []

  def counted(part, weights, gate):
    calls.append((tuple(weights), gate))
    return evaluate(part, weights, gate)

  monkeypatch.setattr(study, "evaluateSealModel", counted)
  cached = study.selectOpenWeightModel(days, priorResult=prior)
  assert cached["comparisons"] == fresh["comparisons"]
  assert cached["selected"] == fresh["selected"]
  assert cached["reusedCombinations"] == 30 and cached["newCombinations"] == 75
  assert len(calls) == 156  # 六份配对基准加七十五组的两段。
  assert prior == original


@pytest.mark.parametrize("corruption", ["duplicate", "weights", "baseline", "failures", "missing"])
def testBadPriorCacheRejected(corruption):
  days = allSealedDays()
  prior = selectSealModel(days)
  if corruption == "duplicate":
    prior["comparisons"].append(deepcopy(prior["comparisons"][0]))
  elif corruption == "weights":
    prior["comparisons"][0]["weights"] = (0.1, 0.2, 0.3, 0.4)
  elif corruption == "baseline":
    prior["comparisons"][0]["train"]["baseline"]["recordedSealCount"] = 0
  elif corruption == "failures":
    prior["comparisons"][0]["train"]["failures"] = ["编造原因"]
  else:
    prior["comparisons"].pop()
  with pytest.raises(ValueError, match="缓存"):
    study.selectOpenWeightModel(days, priorResult=prior)


def testEqualWeightOnlySuccessIsMarkedBaselineNotOptimization():
  days = allSealedDays()
  for item in days.values():
    for row in item["rows"]:
      row["outcome"]["downsidePct"] = 1.0
  result = study.selectOpenWeightModel(days)
  # 所有组合指标相同时优先保留等权，不能按名称挑一个虚假的新最优。
  assert result["status"] == "baseline_retained"
  assert result["selected"]["isEqualWeight"]
  assert not result["riskImprovedAgainstPairedBaseline"]


@pytest.mark.parametrize("day", ["2026-06-17", "2026-09-29", "坏日期", None])
def testOldHoldoutAndInvalidDatesRejected(day):
  with pytest.raises(ValueError, match="开发区间"):
    study.selectOpenWeightModel({day: {"grade": "B", "rows": []}})


def testNoSealEventsStillCannotPass():
  result = study.selectOpenWeightModel(daysFixture())
  assert result["status"] == "no_eligible_development_model"
  assert result["selected"] is None
  assert result["newCombinations"] == 105
