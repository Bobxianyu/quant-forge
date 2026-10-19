"""面板构建不得按 T 日结果或当前证券资格筛选候选。"""

from copy import deepcopy

import pytest
from test_observationStudy import historyRows

from quant_forge.research.observationPanel import buildCodePanel, supportedCode


def fixtureData():
  """一只股票、一日观察，T 行缺失。"""
  rows = historyRows()
  calendar = [r["date"] for r in rows] + ["2025-08-22"]
  pools = {day: {"up": set(), "break": set()} for day in calendar}
  return rows, calendar, pools


def testMissingTargetDoesNotRemoveCandidate():
  rows, calendar, pools = fixtureData()
  panel, excluded = buildCodePanel("000001.SZ", rows, calendar, pools, set(), [calendar[-1]])
  assert len(panel) == 1 and not excluded
  assert panel[0]["signalDate"] == "2025-08-21"
  assert panel[0]["downsidePct"] is None


def testTargetChangesNeverChangeFeaturesOrEligibility():
  rows, calendar, pools = fixtureData()
  original, _ = buildCodePanel("000001.SZ", rows, calendar, pools, set(), [calendar[-1]])
  target = {**rows[-1], "date": calendar[-1], "high_price": 200, "close_price": 190}
  changed, _ = buildCodePanel("000001.SZ", [*rows, target], calendar, pools, {calendar[-1]}, [calendar[-1]])
  for field in ["code", "signalDate", "downside", "momentum", "position", "liquidity"]:
    assert changed[0][field] == original[0][field]
  assert changed[0]["upsidePct"] != original[0]["upsidePct"]


@pytest.mark.parametrize("pool", ["up", "break"])
def testPreviousBothKindsExcludedButCurrentPositiveLabelRetained(pool):
  rows, calendar, pools = fixtureData()
  pools[calendar[-2]][pool].add("000001.SZ")
  panel, excluded = buildCodePanel("000001.SZ", rows, calendar, pools, set(), [calendar[-1]])
  assert not panel and excluded == {"PREVIOUS_POOL_EVENT": 1}
  pools[calendar[-2]][pool].clear()
  pools[calendar[-1]][pool].add("000001.SZ")
  panel, _ = buildCodePanel("000001.SZ", rows, calendar, pools, set(), [calendar[-1]])
  assert panel[0]["touchEvent"] is True


def testMissingPriorPoolFailsInsteadOfAssumingEmpty():
  rows, calendar, pools = fixtureData()
  del pools[calendar[-2]]
  with pytest.raises(ValueError, match="池"):
    buildCodePanel("000001.SZ", rows, calendar, pools, set(), [calendar[-1]])


def testDuplicateBarsAndCalendarRejected():
  rows, calendar, pools = fixtureData()
  with pytest.raises(ValueError, match="重复"):
    buildCodePanel("000001.SZ", rows + [rows[0]], calendar, pools, set(), [calendar[-1]])
  with pytest.raises(ValueError, match="日历"):
    buildCodePanel("000001.SZ", rows, calendar + [calendar[-1]], pools, set(), [calendar[-1]])


def testMissingHistoryAndUnsupportedCodesHaveExplicitCounts():
  rows, calendar, pools = fixtureData()
  _, excluded = buildCodePanel("000001.SZ", rows[:-1], calendar, pools, set(), [calendar[-1]])
  assert excluded == {"MISSING_HISTORY": 1}
  _, excluded = buildCodePanel("300001.SZ", rows, calendar, pools, set(), [calendar[-1]])
  assert excluded == {"OUTSIDE_CODE_SCOPE": 1}


@pytest.mark.parametrize("code,expected", [("000001.SZ", True), ("603056.SH", True), ("605001.SH", True),
  ("688001.SH", False), ("300001.SZ", False), ("000001.SH", False), ("xyz", False)])
def testCodeScope(code, expected):
  assert supportedCode(code) is expected


def testPanelDoesNotMutateInput():
  rows, calendar, pools = fixtureData()
  before = deepcopy((rows, calendar, pools))
  buildCodePanel("000001.SZ", rows, calendar, pools, set(), [calendar[-1]])
  assert (rows, calendar, pools) == before
