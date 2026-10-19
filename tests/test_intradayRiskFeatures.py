"""历史盘中风险特征的手算、时间边界和非法输入验证。"""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from quant_forge.research.intradayRiskFeatures import (
  HISTORY_DAYS,
  RISK_VARIANTS,
  buildRiskFeatureRows,
  computeIntradayRisk,
)


def historyFixture():
  """生成 20 根风险依次为 0% 至 19% 的日线和紧随其后的观察日。"""
  start = date(2025, 9, 1)
  dates = [(start + timedelta(days=index)).isoformat() for index in range(20)]
  rows = [{"date": day, "open_price": 100, "low_price": 100 - index}
          for index, day in enumerate(dates)]
  return rows, dates, (start + timedelta(days=20)).isoformat()


def testHandCalculatedMeanAndWorstTwoAreReturnedWithoutMutation():
  history, dates, target = historyFixture()
  original = deepcopy((history, dates))
  result = computeIntradayRisk(history, dates, target)
  assert HISTORY_DAYS == 20
  assert RISK_VARIANTS == ("intradayMean20", "intradayTail20")
  assert result == pytest.approx({"intradayMean20": 9.5, "intradayTail20": 18.5})
  assert (history, dates) == original


def testFlatBarsAndTupleInputsAreAccepted():
  history, dates, target = historyFixture()
  for row in history:
    row["low_price"] = row["open_price"]
  assert computeIntradayRisk(tuple(history), tuple(dates), target) == {
    "intradayMean20": 0.0, "intradayTail20": 0.0,
  }


def testHistoryOrderDoesNotSubstituteForTailSorting():
  history, dates, target = historyFixture()
  history[0]["low_price"], history[-1]["low_price"] = history[-1]["low_price"], history[0]["low_price"]
  assert computeIntradayRisk(history, dates, target)["intradayTail20"] == pytest.approx(18.5)


@pytest.mark.parametrize("argument,value", [
  ("history", None), ("history", {}), ("history", "无效"),
  ("expectedDates", None), ("expectedDates", {}), ("expectedDates", "无效"),
])
def testNonSequenceInputsRaiseChineseValueError(argument, value):
  history, dates, target = historyFixture()
  arguments = {"history": history, "expectedDates": dates, "targetDate": target}
  arguments[argument] = value
  with pytest.raises(ValueError, match="历史|日期"):
    computeIntradayRisk(**arguments)


@pytest.mark.parametrize("argument,count", [("history", 19), ("history", 21),
                                            ("expectedDates", 19), ("expectedDates", 21)])
def testExactTwentyRowsAndDatesAreRequired(argument, count):
  history, dates, target = historyFixture()
  arguments = {"history": history, "expectedDates": dates, "targetDate": target}
  values = arguments[argument]
  arguments[argument] = values[:count] if count < 20 else [*values, values[-1]]
  with pytest.raises(ValueError, match="20"):
    computeIntradayRisk(**arguments)


@pytest.mark.parametrize("value", [None, True, 20250921, "20250921", "2025-9-21", "2025-02-30", "无效"])
def testTargetMustBeCanonicalIsoDate(value):
  history, dates, _ = historyFixture()
  with pytest.raises(ValueError, match="日期"):
    computeIntradayRisk(history, dates, value)


@pytest.mark.parametrize("value", [None, True, "20250904", "2025-02-30", "无效"])
def testReferenceDatesMustBeCanonicalIsoDates(value):
  history, dates, target = historyFixture()
  dates[3] = value
  history[3]["date"] = value
  with pytest.raises(ValueError, match="日期"):
    computeIntradayRisk(history, dates, target)


@pytest.mark.parametrize("mode", ["duplicate", "reversed", "target", "future", "misaligned"])
def testDateAlignmentAndPastOnlyBoundary(mode):
  history, dates, target = historyFixture()
  if mode == "duplicate":
    dates[3] = dates[2]
    history[3]["date"] = dates[3]
  elif mode == "reversed":
    history.reverse()
    dates.reverse()
  elif mode in {"target", "future"}:
    dates[-1] = target if mode == "target" else "2025-09-22"
    history[-1]["date"] = dates[-1]
  else:
    history[3]["date"] = dates[2]
  with pytest.raises(ValueError, match="日期|观察日"):
    computeIntradayRisk(history, dates, target)


@pytest.mark.parametrize("value", [None, [], "无效", {"open_price": 100, "low_price": 90}])
def testHistoryRowsMustBeDictionariesWithAlignedDate(value):
  history, dates, target = historyFixture()
  history[3] = value
  with pytest.raises(ValueError, match="历史|日期"):
    computeIntradayRisk(history, dates, target)


@pytest.mark.parametrize("field", ["open_price", "low_price"])
@pytest.mark.parametrize("value", [0, -1, True, False, None, "100", float("nan"),
                                    float("inf"), float("-inf"), 10 ** 400])
def testPricesMustBePositiveFiniteNumbers(field, value):
  history, dates, target = historyFixture()
  history[3][field] = value
  with pytest.raises(ValueError, match="价格"):
    computeIntradayRisk(history, dates, target)


@pytest.mark.parametrize("field", ["open_price", "low_price"])
def testMissingPriceIsNotFilled(field):
  history, dates, target = historyFixture()
  del history[3][field]
  with pytest.raises(ValueError, match="价格"):
    computeIntradayRisk(history, dates, target)


def testLowAboveOpeningIsRejected():
  history, dates, target = historyFixture()
  history[3]["low_price"] = 101
  with pytest.raises(ValueError, match="最低价"):
    computeIntradayRisk(history, dates, target)


def testUnrelatedLabelsCannotChangePastRisk():
  history, dates, target = historyFixture()
  expected = computeIntradayRisk(history, dates, target)
  for row in history:
    row.update(sealEvent=True, futureDownside=-999, close_price=None)
  assert computeIntradayRisk(history, dates, target) == expected


def featureRowsFixture():
  """使用间隔两天的参考日历，避免将窗口误写为自然日窗口。"""
  start = date(2025, 8, 1)
  calendar = [(start + timedelta(days=index * 2)).isoformat() for index in range(23)]
  bars = [{"date": day, "open_price": 100, "low_price": 100 - index}
          for index, day in enumerate(calendar)]
  return bars, calendar


def testBuildRowsUsesTwentyReferenceDaysAndPreservesInput():
  bars, calendar = featureRowsFixture()
  targets = calendar[20:22]
  original = deepcopy((bars, calendar, targets))
  result = buildRiskFeatureRows("000001.SZ", bars, calendar, targets)
  assert len(result) == 2
  assert result[0] == {"date": targets[0], "code": "000001.SZ", "historyStart": calendar[0],
                       "historyEnd": calendar[19], "intradayMean20": 9.5, "intradayTail20": 18.5}
  assert result[1]["historyStart"] == calendar[1]
  assert result[1]["historyEnd"] == calendar[20]
  assert result[1]["intradayMean20"] == pytest.approx(10.5)
  assert result[1]["intradayTail20"] == pytest.approx(19.5)
  assert (bars, calendar, targets) == original


def testTargetAndFutureBarPricesCannotAffectTargetFeature():
  bars, calendar = featureRowsFixture()
  target = calendar[20]
  expected = buildRiskFeatureRows("000001.SZ", bars, calendar, [target])
  for row in bars[20:]:
    row.update(open_price=None, low_price=float("nan"))
  assert buildRiskFeatureRows("000001.SZ", bars, calendar, [target]) == expected
  assert buildRiskFeatureRows("000001.SZ", bars[:20], calendar, [target]) == expected


def testMissingRequiredBarLocatesSecurityAndTarget():
  bars, calendar = featureRowsFixture()
  del bars[4]
  with pytest.raises(ValueError, match=f"000001.SZ.*{calendar[20]}"):
    buildRiskFeatureRows("000001.SZ", bars, calendar, [calendar[20]])


@pytest.mark.parametrize("source", ["bars", "calendar", "targets"])
def testRepeatedDatesAreRejectedByBuilder(source):
  bars, calendar = featureRowsFixture()
  targets = [calendar[20]]
  if source == "bars":
    bars.append(bars[0])
  elif source == "calendar":
    calendar.append(calendar[-1])
  else:
    targets.append(targets[0])
  with pytest.raises(ValueError, match="重复|递增"):
    buildRiskFeatureRows("000001.SZ", bars, calendar, targets)


@pytest.mark.parametrize("target", ["2025-08-02", "2025-08-01", "2025-08-03"])
def testMissingCalendarTargetOrInsufficientPriorDaysIsRejected(target):
  bars, calendar = featureRowsFixture()
  with pytest.raises(ValueError, match=f"000001.SZ.*{target}"):
    buildRiskFeatureRows("000001.SZ", bars, calendar, [target])


def testEmptyTargetListReturnsEmptyResult():
  bars, calendar = featureRowsFixture()
  assert buildRiskFeatureRows("000001.SZ", bars, calendar, []) == []


def testShuffledBarsAreLookedUpByDate():
  bars, calendar = featureRowsFixture()
  expected = buildRiskFeatureRows("000001.SZ", bars, calendar, [calendar[20]])
  assert buildRiskFeatureRows("000001.SZ", list(reversed(bars)), calendar, [calendar[20]]) == expected


def testInvalidRequiredPriceIdentifiesSecurityAndTarget():
  bars, calendar = featureRowsFixture()
  bars[0]["low_price"] = 0
  with pytest.raises(ValueError, match=f"000001.SZ.*{calendar[20]}.*价格"):
    buildRiskFeatureRows("000001.SZ", bars, calendar, [calendar[20]])


@pytest.mark.parametrize("argument,value", [
  ("code", None), ("code", ""), ("bars", None), ("calendar", None), ("targetDates", None),
  ("bars", [None]), ("bars", [{"date": "20250801"}]),
  ("calendar", ["20250801"]), ("calendar", ["2025-08-03", "2025-08-01"]),
  ("targetDates", ["20250801"]),
])
def testBuilderMalformedInputsRaiseChineseValueError(argument, value):
  bars, calendar = featureRowsFixture()
  arguments = {"code": "000001.SZ", "bars": bars, "calendar": calendar, "targetDates": [calendar[20]]}
  arguments[argument] = value
  with pytest.raises(ValueError, match="证券|日期|日线|日历|观察"):
    buildRiskFeatureRows(**arguments)
