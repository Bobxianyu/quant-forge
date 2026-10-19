"""封板相对命中门槛与公平配对的回归测试。"""

from copy import deepcopy

import pytest
from test_observationStudy import daysFixture

from quant_forge.research.sealHitStudy import checkSealGate, evaluateSealModel, selectSealModel


def enoughMetrics():
  """满足预设样本门槛的摘要；事件必须分散而非挤在一个日期。"""
  return {"observedDays": 30, "recommendedDays": 30, "environmentValidDays": 60,
          "selectedCount": 300, "validOutcomeCount": 300, "recordedSealCount": 12,
          "recordedSealDays": 6, "outcomeCoveragePct": 100.0, "meanDownsidePct": 1.0,
          "tailDownsidePct": 2.0, "meanUpsidePct": 2.0,
          "selectionShape": [(f"2026-04-{i:02}", 10) for i in range(1, 31)]}


def testExactlyEqualRatePassesAndLesserRateFails():
  candidate, baseline = enoughMetrics(), enoughMetrics()
  assert checkSealGate(candidate, baseline) == []
  candidate["recordedSealCount"] = 11
  assert "SEAL_RATE_BELOW_BASELINE" in checkSealGate(candidate, baseline)


@pytest.mark.parametrize("field,value,reason", [
  ("observedDays", 29, "INSUFFICIENT_OBSERVED_DAYS"),
  ("selectedCount", 299, "INSUFFICIENT_OBSERVATIONS"),
  ("recommendedDays", 23, "INSUFFICIENT_ACTIVE_DAYS"),
  ("outcomeCoveragePct", 98.9, "INSUFFICIENT_OUTCOME_COVERAGE"),
  ("recordedSealCount", 9, "INSUFFICIENT_SEAL_EVENTS"),
  ("recordedSealDays", 4, "CONCENTRATED_SEAL_EVENTS"),
  ("meanDownsidePct", 1.1, "MEAN_RISK_ABOVE_BASELINE"),
  ("tailDownsidePct", 2.1, "TAIL_RISK_ABOVE_BASELINE"),
])
def testEveryGateHasExplicitFailure(field, value, reason):
  candidate, baseline = enoughMetrics(), enoughMetrics()
  candidate[field] = value
  assert reason in checkSealGate(candidate, baseline)


def testZeroVersusZeroIsInsufficientNotSuccess():
  candidate, baseline = enoughMetrics(), enoughMetrics()
  candidate["recordedSealCount"] = baseline["recordedSealCount"] = 0
  assert "INSUFFICIENT_SEAL_EVENTS" in checkSealGate(candidate, baseline)
  assert "BASELINE_INSUFFICIENT_SEAL_EVENTS" in checkSealGate(candidate, baseline)


def testIdenticalCountsOnDifferentDatesAreNotPaired():
  candidate, baseline = enoughMetrics(), enoughMetrics()
  candidate["selectionShape"][0] = ("2026-03-31", 10)
  assert "UNPAIRED_DATES_OR_COUNTS" in checkSealGate(candidate, baseline)


def testMissingAndNonFiniteRiskCannotPass():
  for value in [None, float("nan"), float("inf"), -1, True]:
    candidate = enoughMetrics()
    candidate["tailDownsidePct"] = value
    assert "INVALID_RISK_METRICS" in checkSealGate(candidate, enoughMetrics())


def testTouchWithoutSealIsNotHit():
  days = daysFixture()
  for item in days.values():
    for row in item["rows"]:
      row["outcome"]["touchEvent"] = True
  result = evaluateSealModel(days, (1, 0, 0, 0), "ALL")
  assert result["recordedTouchCount"] == 600
  assert result["recordedSealCount"] == result["recordedSealDays"] == 0
  assert result["trueSealRatePct"] is None


def testEnvironmentMatchedBaselineAndBothPeriodsChecked():
  days = daysFixture()
  for i, item in enumerate(days.values()):
    item["grade"] = "D" if i < 5 else "B"
    for row in item["rows"]:
      row["outcome"]["sealEvent"] = True
  result = selectSealModel(days)
  assert len(result["comparisons"]) == 30
  ab = next(r for r in result["comparisons"] if r["gate"] == "AB")
  assert ab["train"]["candidate"]["selectionShape"] == ab["train"]["baseline"]["selectionShape"]
  assert ab["train"]["baseline"]["selectedCount"] == 250
  assert "INSUFFICIENT_OBSERVED_DAYS" in ab["train"]["failures"]
  assert not ab["eligible"]


def testEligibleModelSelectionDoesNotMutateInputs():
  days = daysFixture()
  for item in days.values():
    for row in item["rows"]:
      row["outcome"]["sealEvent"] = True
  before = deepcopy(days)
  result = selectSealModel(days)
  assert result["status"] == "development_candidate_selected"
  assert result["selected"]["weights"][0] >= 0.5
  assert days == before
  assert result["freshHoldoutStatus"] == "not_evaluated"


def testOldHoldoutAndAnyFutureDateRejected():
  for target in ["2026-06-17", "2026-09-29", "2027-01-01"]:
    with pytest.raises(ValueError, match="开发区间"):
      selectSealModel({target: {"grade": "B", "rows": []}})


def testNoCandidateDoesNotRelaxGate():
  result = selectSealModel(daysFixture())
  assert result["status"] == "no_eligible_development_model"
  assert result["selected"] is None


@pytest.mark.parametrize("shape", [None, [("坏日期", 300)], [("2026-04-01", True)],
                                      [("2026-04-01", 150), ("2026-04-01", 150)]])
def testMalformedShapeCannotBePaired(shape):
  candidate = enoughMetrics()
  candidate["selectionShape"] = shape
  assert "UNPAIRED_DATES_OR_COUNTS" in checkSealGate(candidate, enoughMetrics())


def testInvalidCountsCoverageAndTypes():
  candidate = enoughMetrics()
  candidate["recordedSealCount"] = True
  assert "INVALID_COUNTS" in checkSealGate(candidate, enoughMetrics())
  candidate = enoughMetrics()
  candidate["outcomeCoveragePct"] = None
  assert "INSUFFICIENT_OUTCOME_COVERAGE" in checkSealGate(candidate, enoughMetrics())
  with pytest.raises(ValueError, match="字典"):
    checkSealGate([], {})
  with pytest.raises(ValueError, match="开发区间"):
    selectSealModel({"坏日期": {}})
  candidate = enoughMetrics()
  candidate["tailDownsidePct"] = 10 ** 1000
  assert "INVALID_RISK_METRICS" in checkSealGate(candidate, enoughMetrics())
