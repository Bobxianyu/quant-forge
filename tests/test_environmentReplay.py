"""环境规则历史回放的因果关系、数据闸门与统计验证。"""

import json
from datetime import date, timedelta

import pytest

FIRST_INDEX = "883994.TI"
SECOND_INDEX = "883997.TI"
MULTI_INDEX = "883410.TI"
LIMIT_INDEX = "883900.TI"
INDEX_CODES = (FIRST_INDEX, SECOND_INDEX, MULTI_INDEX, LIMIT_INDEX)
START_DATE = date(2026, 1, 5)
BASE_CLOSE = 100.0


def makeCloses(firstReturns, otherReturns=None):
  """从已知单期涨跌幅构造四指数，日期顺序就是测试参考日历。"""
  returnsByIndex = {code: firstReturns for code in INDEX_CODES}
  returnsByIndex.update(otherReturns or {})
  result = {}
  for code, returns in returnsByIndex.items():
    values = {START_DATE: BASE_CLOSE}
    for offset, returnPct in enumerate(returns, start=1):
      previous = values[START_DATE + timedelta(days=offset - 1)]
      values[START_DATE + timedelta(days=offset)] = previous * (1 + returnPct / 100)
    result[code] = values
  return result


def runReplay(closes, startOffset=2, endOffset=2, **kwargs):
  """用固定区间调用真实研究函数，不替换生产环境规则。"""
  from quant_forge.research.environmentReplay import replayEnvironment

  return replayEnvironment(
    closes,
    evaluationStart=START_DATE + timedelta(days=startOffset),
    evaluationEnd=START_DATE + timedelta(days=endOffset),
    **kwargs,
  )


def testPriorDayFeaturesDriveActualEnvironmentRules():
  """T 日首板暴跌只改变观察结果，不能回写 D 日强势环境输入。"""
  closes = makeCloses([3, -10], {SECOND_INDEX: [1, -90], MULTI_INDEX: [1, -90]})
  result = runReplay(closes)

  assert result["summary"]["sampleCount"] == 1
  row = result["observations"][0]
  assert row["targetDate"] == "2026-01-07"
  assert row["signalDate"] == "2026-01-06"
  assert row["previousDate"] == "2026-01-05"
  assert row["featureReturnsPct"][FIRST_INDEX] == pytest.approx(3)
  assert row["permissionGrade"] == "A"
  assert row["environment"] == "PROFIT_EFFECT"
  assert row["marketStyle"] == "FIRST_BOARD_ARBITRAGE"
  assert row["targetReturnPct"] == pytest.approx(-10)
  assert result["groups"]["permissionGrade"]["A"]["sampleCount"] == 1


def testTargetOtherIndicesAndFuturePricesCannotAlterSignal():
  """T 日非首板数据缺失或未来价格变化，不影响已经固定的信号与标签。"""
  closes = makeCloses([3, -10, 5])
  expected = runReplay(closes)
  targetDate = START_DATE + timedelta(days=2)
  futureDate = START_DATE + timedelta(days=3)
  for code in INDEX_CODES:
    closes[code][futureDate] = float("nan")
    if code != FIRST_INDEX:
      del closes[code][targetDate]

  actual = runReplay(closes)
  assert actual == expected


def testMissingSignalDayInvalidatesBothFeatureSidesWithoutBridging():
  """某指数缺 D 日时，随后两个目标日分别缺特征终点和起点。"""
  closes = makeCloses([3, 3, 3, 3, 3])
  missingDate = START_DATE + timedelta(days=2)
  del closes[SECOND_INDEX][missingDate]
  result = runReplay(closes, startOffset=2, endOffset=5)

  assert [row["targetDate"] for row in result["observations"]] == ["2026-01-07", "2026-01-10"]
  assert [row["targetDate"] for row in result["excluded"]] == ["2026-01-08", "2026-01-09"]
  reasons = [row["reasons"][0] for row in result["excluded"]]
  assert [reason["role"] for reason in reasons] == ["featureSignal", "featurePrevious"]
  assert all(reason["code"] == "MISSING_CLOSE" for reason in reasons)
  assert all(reason["date"] == missingDate.isoformat() for reason in reasons)


@pytest.mark.parametrize("invalidClose", [0, -1, float("nan"), float("inf"), None, True, "100"])
@pytest.mark.parametrize("dateOffset", [0, 1])
def testRejectsInvalidCloseOnEitherFeatureSide(invalidClose, dateOffset):
  """特征起止两端都必须是有限正数，布尔值和文本不能冒充价格。"""
  closes = makeCloses([3, 3])
  closes[SECOND_INDEX][START_DATE + timedelta(days=dateOffset)] = invalidClose
  result = runReplay(closes)

  assert result["summary"]["sampleCount"] == 0
  assert result["excluded"][0]["reasons"][0]["code"] == "INVALID_CLOSE"
  assert result["excluded"][0]["reasons"][0]["indexCode"] == SECOND_INDEX
  json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("dateOffset", [0, 1])
def testDefaultPrecisionGateChecksBothFeatureCloses(dateOffset):
  """研究精度门槛默认一，不能只检查当前收盘而忽略分母。"""
  closes = makeCloses([3, 3])
  closes[MULTI_INDEX][START_DATE + timedelta(days=dateOffset)] = 0.5
  result = runReplay(closes)

  assert result["excluded"][0]["reasons"][0]["code"] == "BELOW_MIN_INDEX_CLOSE"
  assert result["minIndexClose"] == 1.0


def testPrecisionThresholdIsExplicitAndInclusive():
  """阈值可显式调整，恰好达到阈值的正数价格允许研究计算。"""
  closes = {code: {day: value / 200 for day, value in series.items()} for code, series in makeCloses([3, 3]).items()}
  assert runReplay(closes)["summary"]["sampleCount"] == 0
  result = runReplay(closes, minIndexClose=0.5)
  assert result["summary"]["sampleCount"] == 1
  assert result["observations"][0]["permissionGrade"] == "A"


@pytest.mark.parametrize("targetClose, expectedCode", [(0, "INVALID_CLOSE"), (float("nan"), "INVALID_CLOSE"),
                                                     (0.5, "BELOW_MIN_INDEX_CLOSE")])
def testInvalidTargetFirstCloseDoesNotBecomeZeroReturn(targetClose, expectedCode):
  """结果首板价格也必须过数据门槛，缺陷样本不能计入收益分母。"""
  closes = makeCloses([3, 3])
  closes[FIRST_INDEX][START_DATE + timedelta(days=2)] = targetClose
  result = runReplay(closes)

  assert result["summary"]["sampleCount"] == 0
  assert result["excluded"][0]["reasons"][0]["role"] == "target"
  assert result["excluded"][0]["reasons"][0]["code"] == expectedCode


def testFixedBoundsUseEarlierHistoryWithoutReportingOutsideDates():
  """评估区间之外的历史仍可构造 P/D，但只输出区间内的 T。"""
  result = runReplay(makeCloses([3, 3, 3, 3]), startOffset=3, endOffset=3)
  assert result["referenceDateCount"] == 1
  assert [row["targetDate"] for row in result["observations"]] == ["2026-01-08"]
  assert result["observations"][0]["previousDate"] == "2026-01-06"


def testInsufficientWarmupDatesAreExplicitlyExcluded():
  """参考日历最初两天缺少完整 P/D，不得默默丢掉排除原因。"""
  result = runReplay(makeCloses([3, 3]), startOffset=0, endOffset=2)
  assert result["referenceDateCount"] == 3
  assert result["summary"]["sampleCount"] == 1
  assert len(result["excluded"]) == 2
  assert all(row["reasons"][0]["code"] == "INSUFFICIENT_HISTORY" for row in result["excluded"])


def testEmptySampleGroupsUseNullStatisticsAndRemainJsonSafe():
  """空组的收益统计不可填零；全部权限与环境仍提供零样本统计。"""
  result = runReplay({code: {} for code in INDEX_CODES})
  assert result["summary"] == {
    "sampleCount": 0,
    "meanReturnPct": None,
    "medianReturnPct": None,
    "positiveReturnRatePct": None,
    "lowerDecileReturnPct": None,
    "worstReturnPct": None,
  }
  assert set(result["groups"]["permissionGrade"]) == {"A", "B", "C", "D"}
  assert set(result["groups"]["environment"]) == {"PROFIT_EFFECT", "RECOVERY", "EBB_TIDE"}
  assert all(group == result["summary"] for group in result["groups"]["permissionGrade"].values())
  json.dumps(result, allow_nan=False)


def testGroupStatisticsUseIndependentFutureOutcomes():
  """分组输入来自前一天，收益均值和下尾统计来自次日首板标签。"""
  # 四指数同涨跌时，3% 对应 A、1% 对应 B、全零对应 C、负数对应 D。
  result = runReplay(makeCloses([3, 1, 0, -2, 3, 3]), startOffset=2, endOffset=6)
  assert [row["permissionGrade"] for row in result["observations"]] == ["A", "B", "C", "D", "A"]
  stats = result["summary"]
  assert stats["sampleCount"] == 5
  assert stats["meanReturnPct"] == pytest.approx(1)
  assert stats["medianReturnPct"] == pytest.approx(1)
  assert stats["positiveReturnRatePct"] == pytest.approx(60)
  assert stats["lowerDecileReturnPct"] == pytest.approx(-1.2)
  assert stats["worstReturnPct"] == pytest.approx(-2)
  gradeA = result["groups"]["permissionGrade"]["A"]
  assert gradeA["sampleCount"] == 2
  assert gradeA["meanReturnPct"] == pytest.approx(2)
  assert gradeA["lowerDecileReturnPct"] == pytest.approx(1.2)
  assert result["groups"]["environment"]["RECOVERY"]["worstReturnPct"] == pytest.approx(-2)


@pytest.mark.parametrize("minimum", [-1, float("nan"), float("inf"), True, "1"])
def testRejectsInvalidResearchThreshold(minimum):
  """不合法的研究阈值属于调用错误，必须给出中文提示。"""
  with pytest.raises(ValueError, match="minIndexClose"):
    runReplay(makeCloses([3, 3]), minIndexClose=minimum)


def testRejectsReversedEvaluationDates():
  """评估区间倒置必须报错，不返回容易误读的空研究。"""
  with pytest.raises(ValueError, match="评估起止日期"):
    runReplay(makeCloses([3, 3]), startOffset=3, endOffset=2)


def testRejectsMissingRequiredIndexSeries():
  """缺少整个必要指数序列属于输入契约错误。"""
  closes = makeCloses([3, 3])
  del closes[SECOND_INDEX]
  with pytest.raises(ValueError, match="缺少指数"):
    runReplay(closes)


def testRejectsOverflowedFeatureReturn():
  """有限正价格相除后仍可能溢出，不能让无限溢价进入生产判断。"""
  closes = makeCloses([3, 3])
  closes[SECOND_INDEX][START_DATE] = 1.0
  closes[SECOND_INDEX][START_DATE + timedelta(days=1)] = 1e308
  result = runReplay(closes)
  assert result["summary"]["sampleCount"] == 0
  assert result["excluded"][0]["reasons"][0]["code"] == "NON_FINITE_RETURN"
  json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("previousClose, signalClose", [(100.0, 102.0), (77.3, 78.846)])
def testExactStrongThresholdDoesNotBecomeHigherPermission(previousClose, signalClose):
  """十进制行情恰好上涨百分之二时，不能因浮点尾差误判为 A。"""
  closes = {code: {
    START_DATE: previousClose,
    START_DATE + timedelta(days=1): signalClose,
    START_DATE + timedelta(days=2): signalClose,
  } for code in INDEX_CODES}
  row = runReplay(closes)["observations"][0]
  assert row["featureReturnsPct"][FIRST_INDEX] == 2.0
  assert row["permissionGrade"] == "B"


@pytest.mark.parametrize("signalClose, expectedGrade", [(101.999999999, "B"), (102.000000001, "A")])
def testPrecisionHandlingPreservesRealValuesBesideThreshold(signalClose, expectedGrade):
  """处理计算精度不能粗略四舍五入，抹掉阈值两侧的真实差异。"""
  closes = {code: {
    START_DATE: BASE_CLOSE,
    START_DATE + timedelta(days=1): signalClose,
    START_DATE + timedelta(days=2): signalClose,
  } for code in INDEX_CODES}
  assert runReplay(closes)["observations"][0]["permissionGrade"] == expectedGrade


def testFiniteLargeOutcomesDoNotOverflowGroupedMedian():
  """两个合法大数的中位数也必须有限，不能破坏严格 JSON 输出。"""
  prices = [100.0, 100.0, 1e308, 100.0, 100.0, 1e308]
  closes = {code: {START_DATE + timedelta(days=offset): value for offset, value in enumerate(prices)}
            for code in INDEX_CODES}
  result = runReplay(closes, startOffset=2, endOffset=5)
  assert result["groups"]["permissionGrade"]["C"]["medianReturnPct"] == pytest.approx(1e308)
  json.dumps(result, allow_nan=False)
