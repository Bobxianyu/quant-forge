"""用可手算样本验证固定模型跨期差异、共同支持与缺失值。"""

from copy import deepcopy

import pytest

from quant_forge.research import periodShiftDiagnosis as study


def dayRow(day, grade="A", candidateMean=2, baselineMean=1, selected=10, candidateSeals=2, baselineSeals=1):
  """构造一个模型与基准同数量选择的每日记录。"""
  return {"date": day, "grade": grade, "candidateMeanPct": candidateMean, "baselineMeanPct": baselineMean,
          "candidateSelectedCount": selected, "baselineSelectedCount": selected,
          "candidateSealCount": candidateSeals, "baselineSealCount": baselineSeals, "sharedCount": 0}


def decompositionRows():
  """训练环境占比为 3:1、验证为 1:3，并保持每环境结果可手算。"""
  return [
    dayRow("2026-01-01", "A", 2, 2, candidateSeals=1, baselineSeals=1),
    dayRow("2026-01-02", "A", 2, 2, candidateSeals=1, baselineSeals=1),
    dayRow("2026-01-03", "A", 2, 2, candidateSeals=1, baselineSeals=1),
    dayRow("2026-02-01", "B", 6, 2, candidateSeals=5, baselineSeals=1),
    dayRow("2026-04-01", "A", 4, 2, candidateSeals=3, baselineSeals=1),
    dayRow("2026-05-01", "B", 10, 2, candidateSeals=9, baselineSeals=1),
    dayRow("2026-05-02", "B", 10, 2, candidateSeals=9, baselineSeals=1),
    dayRow("2026-05-03", "B", 10, 2, candidateSeals=9, baselineSeals=1),
  ]


def testSymmetricDecompositionMatchesKnownWithinAndMix():
  """环境内变化 3 加上环境占比变化 2.5，等于风险跨期差 5.5。"""
  rows = decompositionRows()
  original = deepcopy(rows)
  result = study.summarizePeriodShift(rows)
  risk = result["riskDecomposition"]
  assert risk["status"] == "ok"
  assert risk["commonGrades"] == ["A", "B"]
  assert risk["trainMean"] == 1
  assert risk["validationMean"] == 6.5
  assert risk["within"] == 3
  assert risk["mix"] == 2.5
  assert risk["total"] == risk["within"] + risk["mix"] == 5.5
  assert risk["byGrade"]["A"]["trainShare"] == 0.75
  assert risk["byGrade"]["A"]["validationShare"] == 0.25
  assert risk["byGrade"]["B"]["trainMean"] == 4
  seal = result["sealDecomposition"]
  assert seal["within"] == 30
  assert seal["mix"] == 25
  assert seal["total"] == 55
  assert rows == original


def testOverallGroupsUsePairedDayRiskAndObservationWeightedSeals():
  """风险按有效日等权，正封板记录差按全部观察数加权。"""
  rows = [dayRow("2026-01-01", "A", 2, 1, 2, 1, 0),
          dayRow("2026-02-01", "B", 6, 1, 8, 0, 1)]
  rows[0]["sharedCount"] = 1
  rows[1]["sharedCount"] = 8
  result = study.summarizePeriodShift(rows)
  overall = result["train"]["overall"]
  assert overall["dayCount"] == overall["pairedDays"] == 2
  assert overall["meanRiskDeltaPct"] == 3
  assert overall["candidateMeanPct"] == 4
  assert overall["baselineMeanPct"] == 1
  assert overall["sealDeltaPp"] == 0
  assert overall["candidateSelectedCount"] == overall["baselineSelectedCount"] == 10
  assert overall["candidateSealCount"] == overall["baselineSealCount"] == 1
  assert overall["candidateRecordedSealLowerBoundPct"] == 10
  assert overall["meanOverlapFraction"] == 0.75
  assert result["train"]["byGrade"]["A"]["meanRiskDeltaPct"] == 1
  assert result["train"]["byMonth"]["2026-02"]["meanRiskDeltaPct"] == 5


def testSealDecompositionWeightsObservationsRatherThanDays():
  """同样日数下观察数的占比变化必须进入封板构成项。"""
  rows = [dayRow("2026-01-01", "A", 1, 1, 10, 1, 1),
          dayRow("2026-02-01", "B", 2, 1, 30, 16, 1),
          dayRow("2026-04-01", "A", 1, 1, 30, 1, 1),
          dayRow("2026-05-01", "B", 2, 1, 10, 6, 1)]
  result = study.summarizePeriodShift(rows)
  seal = result["sealDecomposition"]
  assert seal["within"] == 0
  assert seal["mix"] == seal["total"] == -25
  assert seal["byGrade"]["A"]["trainShare"] == 0.25
  assert seal["byGrade"]["A"]["validationShare"] == 0.75
  assert result["riskDecomposition"]["total"] == 0


def testMissingRiskDoesNotTurnIntoZeroOrRemoveSealEvidence():
  """任一侧风险缺失就排除该配对日，但正封板记录仍保留。"""
  rows = [dayRow("2026-01-01", candidateMean=None, baselineMean=50, candidateSeals=3),
          dayRow("2026-01-02", candidateMean=3, baselineMean=1),
          dayRow("2026-04-01", candidateMean=4, baselineMean=1)]
  result = study.summarizePeriodShift(rows)
  overall = result["train"]["overall"]
  assert overall["pairedDays"] == overall["unpairedDays"] == 1
  assert overall["meanRiskDeltaPct"] == 2
  assert overall["candidateMeanPct"] == 3
  assert overall["baselineMeanPct"] == 1
  assert overall["candidateSealCount"] == 5
  assert overall["candidateSelectedCount"] == 20
  assert overall["sealDeltaPp"] == 15
  support = result["riskDecomposition"]["train"]
  assert support["missingRiskDayCount"] == 1
  assert support["missingRiskObservationCount"] == 10
  assert support["commonDayCount"] == 1
  assert result["sealDecomposition"]["train"]["commonDayCount"] == 2


def testCommonSupportExcludesOneSidedEnvironmentsAndReportsTheirCounts():
  """单边环境不能通过填零影响共同支持分解。"""
  rows = [dayRow("2026-01-01", "A", 3, 1), dayRow("2026-01-02", "B", 100, 1),
          dayRow("2026-04-01", "A", 4, 1), dayRow("2026-04-02", "C", 200, 1)]
  risk = study.summarizePeriodShift(rows)["riskDecomposition"]
  assert risk["commonGrades"] == ["A"]
  assert risk["within"] == risk["total"] == 1
  assert risk["mix"] == 0
  for period in ("train", "validation"):
    assert risk[period]["excludedDayCount"] == 1
    assert risk[period]["excludedObservationCount"] == 10
    assert risk[period]["commonDayCount"] == 1
  assert risk["byGrade"]["B"]["validationMean"] is None
  assert risk["byGrade"]["B"]["trainShare"] is None
  assert risk["byGrade"]["B"]["trainOverallShare"] == 0.5


@pytest.mark.parametrize("rows", [[], [dayRow("2026-01-01")],
  [dayRow("2026-01-01", "A"), dayRow("2026-04-01", "B")]])
def testNoCommonSupportKeepsDecompositionUnknown(rows):
  """空段与互不重叠环境均不得生成零差异。"""
  result = study.summarizePeriodShift(rows)
  for key in ("riskDecomposition", "sealDecomposition"):
    item = result[key]
    assert item["status"] == "insufficient_common_support"
    assert item["commonGrades"] == []
    assert all(item[field] is None for field in ("within", "mix", "total", "trainMean", "validationMean"))


def testAllMissingRiskHasNoRiskSupportButRetainsSealSupport():
  """同环境全风险缺失不等于零风险。"""
  rows = [dayRow("2026-01-01", candidateMean=None), dayRow("2026-04-01", baselineMean=None)]
  result = study.summarizePeriodShift(rows)
  assert result["riskDecomposition"]["status"] == "insufficient_common_support"
  assert result["sealDecomposition"]["status"] == "ok"
  assert result["train"]["overall"]["meanRiskDeltaPct"] is None
  assert result["validation"]["overall"]["baselineMeanPct"] is None


def testEmptyPeriodPreservesUnknownMeansAndEmptyGroups():
  """空段仍有稳定结构，均值与比例保持未知。"""
  result = study.summarizePeriodShift([])
  overall = result["train"]["overall"]
  assert overall["dayCount"] == overall["pairedDays"] == overall["candidateSelectedCount"] == 0
  assert all(overall[key] is None for key in ("meanRiskDeltaPct", "sealDeltaPp", "meanOverlapFraction",
                                             "candidateMeanPct", "candidateRecordedSealLowerBoundPct"))
  assert result["train"]["byGrade"] == result["train"]["byMonth"] == {}
  assert result["leaveOneMonthOut"] == {}


def testLeaveOneMonthOutReusesFixedRowsAndHandlesMonthAcrossBoundary():
  """三月跨分段时同月两边都删除，删除后空段的差保持未知。"""
  rows = [dayRow("2026-02-01", candidateMean=2), dayRow("2026-03-16", candidateMean=10),
          dayRow("2026-03-17", candidateMean=20), dayRow("2026-04-01", candidateMean=4)]
  result = study.summarizePeriodShift(rows)["leaveOneMonthOut"]
  march = result["2026-03"]
  assert march["removedDayCount"] == 2
  assert march["train"]["dayCount"] == march["validation"]["dayCount"] == 1
  assert march["riskShiftPct"] == 2
  assert march["sealShiftPp"] == 0
  single = study.summarizePeriodShift([rows[0], rows[3]])["leaveOneMonthOut"]["2026-02"]
  assert single["train"]["meanRiskDeltaPct"] is None
  assert single["riskShiftPct"] is single["sealShiftPp"] is None


def testSealCountsArePositiveRecordsAndDoNotInventNegativeEvents():
  """未知事件只保留观察分母，结果不能推导出真正失败率。"""
  row = dayRow("2026-01-01", candidateSeals=0, baselineSeals=0)
  overall = study.summarizePeriodShift([row])["train"]["overall"]
  assert overall["candidateSealCount"] == 0
  assert overall["candidateRecordedSealLowerBoundPct"] == 0
  assert overall["candidateSelectedCount"] == 10
  assert "candidateBreakCount" not in overall
  assert "trueSealRatePct" not in overall


@pytest.mark.parametrize("day", ["2026-06-17", "2026-10-03", "2025-09-16", "错误", None, "2026-1-1"])
def testRejectHoldoutFutureAndInvalidDates(day):
  """旧留出、未来及无效日期一律拒绝。"""
  with pytest.raises(ValueError, match="开发区间"):
    study.summarizePeriodShift([dayRow(day)])


@pytest.mark.parametrize("field,value", [
  ("candidateSelectedCount", 0), ("candidateSelectedCount", -1), ("candidateSelectedCount", True),
  ("candidateSelectedCount", 1.5), ("candidateSelectedCount", 9), ("baselineSelectedCount", 9),
  ("candidateSealCount", -1), ("candidateSealCount", 11), ("candidateSealCount", True),
  ("baselineSealCount", None), ("sharedCount", -1), ("sharedCount", 11), ("sharedCount", True),
])
def testRejectInvalidCounts(field, value):
  """数量必须为合法整数、选择数相同且范围一致。"""
  row = dayRow("2026-01-01")
  row[field] = value
  with pytest.raises(ValueError, match="数量|计数"):
    study.summarizePeriodShift([row])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "1", -1])
@pytest.mark.parametrize("field", ["candidateMeanPct", "baselineMeanPct"])
def testRejectInvalidRiskMeans(field, value):
  """缺失风险允许为 None，但非法或非有限风险不允许混入。"""
  row = dayRow("2026-01-01")
  row[field] = value
  with pytest.raises(ValueError, match="风险"):
    study.summarizePeriodShift([row])


def testRejectDuplicateDatesEvenWhenGradesDiffer():
  """同一模型日只能出现一次，避免多个模型混合计数。"""
  with pytest.raises(ValueError, match="重复"):
    study.summarizePeriodShift([dayRow("2026-01-01", "A"), dayRow("2026-01-01", "B")])


@pytest.mark.parametrize("grade", ["E", "AB", "", None, True])
def testRejectUnknownEnvironment(grade):
  """环境必须已冻结为 A 到 D 中的一档。"""
  with pytest.raises(ValueError, match="环境"):
    study.summarizePeriodShift([dayRow("2026-01-01", grade)])


@pytest.mark.parametrize("rows", [None, "错误", [None], [{}]])
def testRejectMalformedInputWithChineseError(rows):
  """结构错误统一提供中文定位，避免泄漏底层 KeyError。"""
  with pytest.raises(ValueError, match="每日|字段"):
    study.summarizePeriodShift(rows)
