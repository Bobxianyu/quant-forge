"""用可手算配对样本验证风险归因和时间隔离。"""

from copy import deepcopy

import pytest
from test_observationStudy import candidate

from quant_forge.research import weightRiskDiagnosis as study
from quant_forge.research.observationStudy import rankCandidates


def pairRows():
  """共同股票抵消，换入高风险股票导致五个百分点差值。"""
  rows = [candidate("A", [1, 1, 1, 1], loss=1), candidate("B", [2, 2, 2, 2], loss=3),
          candidate("C", [3, 3, 3, 3], loss=11)]
  rows[2]["outcome"].update(touchEvent=True, sealEvent=None)
  return rankCandidates(rows)


def testPairedContributionConservesDifferenceAndSharedStocksCancel():
  a, b, c = pairRows()
  before = deepcopy([a, b, c])
  result = study.attributeDay("2026-04-01", "A", [b, c], [a, b])
  assert result["daily"]["deltaPct"] == pytest.approx(5)
  terms = {r["code"]: r for r in result["stocks"]}
  assert terms["B"]["contributionPct"] == 0
  assert terms["A"]["contributionPct"] == -0.5
  assert terms["C"]["contributionPct"] == 5.5
  assert terms["C"]["eventGroup"] == "recordedBreak"
  assert terms["A"]["eventGroup"] == "unrecordedUnknown"
  assert sum(r["contributionPct"] for r in terms.values()) == pytest.approx(5)
  assert [a, b, c] == before


def testMissingResultUsesOwnValidDenominatorWithoutZeroImputation():
  a, b, c = pairRows()
  b["outcome"]["downsidePct"] = None
  result = study.attributeDay("2026-04-01", "A", [b, c], [a, b])
  assert result["daily"]["deltaPct"] == 10
  assert result["daily"]["candidateValidCount"] == result["daily"]["baselineValidCount"] == 1
  assert result["daily"]["candidateSelectedCount"] == 2
  assert result["daily"]["candidateMissingCount"] == 1
  assert sum(r["contributionPct"] for r in result["stocks"]) == 10
  result = study.attributeDay("2026-04-01", "A", [b], [a])
  assert result["daily"]["deltaPct"] is None and result["stocks"] == []


def testCommonStockCanContributeWhenEffectiveDenominatorsDiffer():
  a, b, c = pairRows()
  c["outcome"]["downsidePct"] = None
  result = study.attributeDay("2026-04-01", "A", [b, c], [a, b])
  terms = {r["code"]: r["contributionPct"] for r in result["stocks"]}
  assert result["daily"]["deltaPct"] == 1
  assert terms["B"] == 1.5 and sum(terms.values()) == 1


@pytest.mark.parametrize("loss", [float("nan"), float("inf"), -1, True, "1"])
def testInvalidResultRejected(loss):
  a, b, c = pairRows()
  c["outcome"]["downsidePct"] = loss
  with pytest.raises(ValueError, match="非法下行"):
    study.attributeDay("2026-04-01", "A", [b, c], [a, b])


@pytest.mark.parametrize("day", ["2026-06-17", "2026-10-03", "错误", None])
def testRejectOldHoldoutOrInvalidDate(day):
  with pytest.raises(ValueError, match="开发区间"):
    study.attributeDay(day, "A", [], [])


@pytest.mark.parametrize("problem", ["duplicate", "differentCount", "inconsistentOutcome"])
def testInvalidPairedSelectionRejected(problem):
  a, b, c = pairRows()
  left, right = [b, c], [a, b]
  if problem == "duplicate":
    left = [c, c]
  elif problem == "differentCount":
    right = [a]
  else:
    right = [a, deepcopy(b)]
    right[1]["outcome"]["downsidePct"] = 999
  with pytest.raises(ValueError, match="配对"):
    study.attributeDay("2026-04-01", "A", left, right)


def testGroupingPositiveSharesAndSensitivityDoNotPoolModels():
  a, b, c = pairRows()
  records = [study.attributeDay(f"2026-04-0{i}", grade, left, right) for i, grade, left, right in (
    (1, "A", [b, c], [a, b]), (2, "B", [a, b], [b, c]), (3, "A", [a, c], [a, b]))]
  summary = study.summarizeAttribution(records)
  assert summary["meanDeltaPct"] == pytest.approx(4 / 3)
  assert summary["higherRiskDayFraction"] == pytest.approx(2 / 3)
  assert summary["byGrade"]["A"]["meanDeltaPct"] == 4.5
  assert summary["byGrade"]["B"]["meanDeltaPct"] == -5
  assert summary["topDaysPositiveSharePct"] == 100
  assert summary["withoutTopPositiveDays"]["meanDeltaPct"] == -5
  assert sum(x["meanContributionPct"] for x in summary["stocks"]) == pytest.approx(4 / 3)
  assert sum(x["meanContributionPct"] for x in summary["eventGroups"].values()) == pytest.approx(4 / 3)


def testEmptyAndNoExcessDoNotInventConcentration():
  assert study.summarizeAttribution([])["meanDeltaPct"] is None
  a, b, _ = pairRows()
  result = study.summarizeAttribution([study.attributeDay("2026-04-01", "B", [a, b], [a, b])])
  assert result["topDaysPositiveSharePct"] is None
  assert result["topStocksPositiveSharePct"] is None
  assert result["meanDeltaPct"] == 0


def testDuplicateDatesCannotBeCountedAsIndependentEvidence():
  a, b, _ = pairRows()
  record = study.attributeDay("2026-04-01", "B", [a, b], [a, b])
  with pytest.raises(ValueError, match="重复"):
    study.summarizeAttribution([record, record])


def testTailUsesSeparateWorstDaysRatherThanWorstDifferences():
  a, b, c = pairRows()
  first = study.attributeDay("2026-04-01", "B", [b, c], [a, b])
  second = study.attributeDay("2026-04-02", "B", [a, b], [b, c])
  summary = study.summarizeAttribution([first, second])
  assert summary["candidateTailPct"] == summary["baselineTailPct"] == 7
  assert summary["candidateTailDates"] != summary["baselineTailDates"]


def testFeatureComparisonUsesPremarketRanksAndDoesNotTreatUnknownAsNoSeal():
  a, b, c = pairRows()
  c["outcome"]["sealEvent"] = True
  result = study.summarizeAttribution([study.attributeDay("2026-04-01", "B", [b, c], [a, b])])
  assert result["features"]["momentum"]["candidateRank"] == 0.75
  assert result["features"]["momentum"]["baselineRank"] == 0.25
  assert result["eventGroups"]["recordedSeal"]["candidateCount"] == 1
  assert result["eventGroups"]["unrecordedUnknown"]["candidateCount"] == 1
