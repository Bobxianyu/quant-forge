"""独立盘前观察研究的时间边界、缺失口径和风险选择回归。"""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from quant_forge.research.observationEvaluation import chooseModel, evaluateModel, splitName
from quant_forge.research.observationStudy import (
  FEATURE_NAMES,
  buildFeatures,
  buildOutcome,
  rankCandidates,
  selectCandidates,
  weightGrid,
)


def historyRows():
  """构造连续 21 日可手算日线，成交额单位为元。"""
  return [{"date": (date(2025, 8, 1) + timedelta(days=i)).isoformat(), "open_price": 10.0,
           "high_price": 11.0, "low_price": 9.0, "close_price": 10.0,
           "volume": 20_000_000.0, "turnover": 200_000_000.0} for i in range(21)]


def testFeaturesUseOnlyRequestedHistoryAndNeverMutate():
  rows = historyRows()
  original = deepcopy(rows)
  features, reason = buildFeatures(rows, [r["date"] for r in rows], set())
  assert reason is None
  assert features == dict(zip(FEATURE_NAMES, [0.0, 0.0, 0.5, 200_000_000.0]))
  assert rows == original


@pytest.mark.parametrize("field,value", [
  ("close_price", 0), ("open_price", float("nan")), ("high_price", 8),
  ("low_price", 12), ("volume", 0), ("turnover", -1), ("volume", True),
])
def testInvalidHistoryExcluded(field, value):
  rows = historyRows()
  rows[3][field] = value
  assert buildFeatures(rows, [r["date"] for r in rows], set())[1] == "INVALID_HISTORY"


def testMissingOrMisdatedHistoryNotFilled():
  rows = historyRows()
  dates = [r["date"] for r in rows]
  assert buildFeatures(rows[:-1], dates, set())[1] == "MISSING_HISTORY"
  rows[-1]["date"] = "2099-01-01"
  assert buildFeatures(rows, dates, set())[1] == "MISSING_HISTORY"


def testExDateWindowExcludesEffectivePastButNotFuture():
  rows = historyRows()
  dates = [r["date"] for r in rows]
  assert buildFeatures(rows, dates, {dates[5]})[1] == "EX_DATE_WINDOW"
  assert buildFeatures(rows, dates, {dates[0], "2099-01-01"})[1] is None


def testLiquidityAndPriceFilters():
  rows = historyRows()
  dates = [r["date"] for r in rows]
  for row in rows:
    row["turnover"] = 99_999_999
  assert buildFeatures(rows, dates, set())[1] == "LOW_LIQUIDITY"
  for row in rows:
    row.update(open_price=2, close_price=2, low_price=1, high_price=3, turnover=200_000_000)
  assert buildFeatures(rows, dates, set())[1] == "LOW_PRICE"


def testFlatRangeAndDownsideFormula():
  rows = historyRows()
  rows[-1].update(open_price=9, high_price=9, low_price=9, close_price=9)
  features, _ = buildFeatures(rows, [r["date"] for r in rows], set())
  assert features["downside"] == pytest.approx((100 / 20) ** 0.5)
  assert features["momentum"] == pytest.approx(-10)
  assert features["position"] == pytest.approx(0.5)


def testOutcomeDoesNotInventNegativeEventsOrReplaceMissingPrices():
  missing = buildOutcome(None, False, False)
  assert missing["downsidePct"] is None and missing["touchEvent"] is None
  assert buildOutcome(None, True, False)["sealEvent"] is True
  normal = buildOutcome(historyRows()[0], False, True)
  assert normal["downsidePct"] == pytest.approx(10)
  assert normal["upsidePct"] == pytest.approx(10)
  assert normal["closePct"] == 0
  assert normal["sealEvent"] is None and normal["touchEvent"] is True
  with pytest.raises(ValueError, match="双池"):
    buildOutcome(None, True, True)


def candidate(code, values, loss=1, upside=2):
  """生成含标签的候选，标签不参与排名。"""
  return {"code": code, "features": dict(zip(FEATURE_NAMES, values)),
          "outcome": {"downsidePct": loss, "upsidePct": upside, "closePct": 0,
                      "touchEvent": None, "sealEvent": None}}


def testAverageRanksDirectionAndFutureLabelsCannotChangeSelection():
  rows = [candidate("000001.SZ", [1, 2, 3, 4]), candidate("000002.SZ", [1, 3, 3, 5]),
          candidate("000003.SZ", [3, 1, 3, 3])]
  ranked = rankCandidates(rows)
  assert ranked[0]["ranks"] == pytest.approx([0.75, 0.5, 0.5, 0.5])
  assert "ranks" not in rows[0]
  assert selectCandidates(ranked, (1, 0, 0, 0), top=1)[0]["code"] == "000001.SZ"
  rows[0]["outcome"]["downsidePct"] = 999
  assert selectCandidates(rankCandidates(rows), (1, 0, 0, 0), top=1)[0]["code"] == "000001.SZ"
  assert rankCandidates(rows[:1])[0]["ranks"] == (0.5, 0.5, 0.5, 0.5)
  assert rankCandidates([]) == []


@pytest.mark.parametrize("weights", [(1, 0), (-1, 1, 1, 0), (True, 0, 0, 0),
                                      (float("nan"), 0, 0, 0), (0, 0, 0, 0), ("1", 0, 0, 0)])
def testInvalidWeightsCannotBypassEmptyCandidates(weights):
  with pytest.raises(ValueError, match="权重"):
    selectCandidates([], weights)


def testWeightGridAndCandidateShortage():
  grid = weightGrid()
  assert len(grid) == len(set(grid)) == 10
  assert all(sum(w) == 1 and w[0] >= 0.5 for w in grid)
  assert selectCandidates(rankCandidates([candidate("000001.SZ", [1, 2, 3, 4])]), grid[0]) == []
  with pytest.raises(ValueError, match="数量"):
    selectCandidates([], grid[0], top=True)


def daysFixture():
  """训练及验证各 30 日；低波动排名的股票在结果中也具有更小风险。"""
  days = {}
  for start in [date(2025, 10, 1), date(2026, 4, 1)]:
    for i in range(30):
      rows = [candidate(f"{j:06}.SZ", [j, j, j, j], loss=1 + j / 10, upside=2) for j in range(20)]
      days[(start + timedelta(days=i)).isoformat()] = {"grade": "B", "rows": rankCandidates(rows)}
  return days


@pytest.mark.parametrize("day,expected", [("2025-09-17", "train"), ("2026-03-16", "train"),
  ("2026-03-17", "validation"), ("2026-06-16", "validation"),
  ("2026-06-17", "holdout"), ("2026-09-16", "holdout")])
def testTimeSplitBoundaries(day, expected):
  assert splitName(day) == expected


def testInvalidSplitAndFutureDatesRejectedBySelector():
  with pytest.raises(ValueError, match="区间"):
    splitName("2025-09-16")
  days = daysFixture()
  days["2026-06-17"] = next(iter(days.values()))
  with pytest.raises(ValueError, match="留出"):
    chooseModel(days)


def testRiskSelectionAndLockedInputs():
  days = daysFixture()
  result = chooseModel(days)
  assert result["status"] == "selected"
  assert result["selected"]["weights"][0] >= 0.5
  assert len(result["comparisons"]) == 30
  assert result["selected"]["validation"]["tailDownsidePct"] <= result["baseline"]["validation"]["tailDownsidePct"]


def testNoEligibleModelDoesNotRelaxThresholds():
  days = dict(list(daysFixture().items())[:1])
  result = chooseModel(days)
  assert result["status"] == "no_eligible_model" and result["selected"] is None


def testEnvironmentBlockedAndUnknownAreNotZeroLoss():
  days = daysFixture()
  keys = list(days)
  days[keys[0]]["grade"] = "D"
  days[keys[1]]["grade"] = None
  metrics = evaluateModel(days, (1, 0, 0, 0), "ABC")
  assert metrics["blockedDays"] == 1 and metrics["unknownEnvironmentDays"] == 1
  assert metrics["observedDays"] == 58
  assert metrics["meanDownsidePct"] == pytest.approx(1.45)
  assert metrics["recordedTouchCount"] == 0
  assert metrics["trueTouchRatePct"] is None
  assert metrics["historicalEligibilityUnknownCount"] == metrics["selectedCount"]


def testMissingLabelsKeepSelectionDenominatorAndEmptyStatisticsAreNull():
  days = daysFixture()
  for item in days.values():
    item["rows"][0]["outcome"] = buildOutcome(None, False, False)
  metrics = evaluateModel(days, (1, 0, 0, 0), "ALL")
  assert metrics["selectedCount"] == 600 and metrics["validOutcomeCount"] == 540
  assert metrics["missingOutcomeCount"] == 60
  assert metrics["outcomeCoveragePct"] == 90
  assert evaluateModel({}, (1, 0, 0, 0), "ALL")["tailDownsidePct"] is None
  with pytest.raises(ValueError, match="环境"):
    evaluateModel(days, (1, 0, 0, 0), "BAD")
