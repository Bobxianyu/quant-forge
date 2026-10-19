"""用手算样本验证盘前特征分布、标签缺失和开发日期隔离。"""

from copy import deepcopy

import pytest

from quant_forge.research import featureDriftDiagnosis as study

FEATURE_FIELDS = ("intradayMean20", "intradayTail20", "momentum", "position", "liquidity")
TRAIN_DAY = "2025-09-17"
SECOND_TRAIN_DAY = "2025-09-18"
VALIDATION_DAY = "2026-03-17"


def makeRow(code, value=5, *, day=TRAIN_DAY, loss=None, seal=None, touch=None):
  """构造五项特征相同的合成记录，默认事件和结果均未知。"""
  return {"date": day, "code": code, "features": dict.fromkeys(FEATURE_FIELDS, value),
          "downsidePct": loss, "sealEvent": seal, "touchEvent": touch}


def allKeys(rows):
  """生成上下文中的证券日键，不对输入做任何修改。"""
  return {(row["date"], row["code"]) for row in rows}


def testLinearQuartilesUseAllTrainRowsAndValuesOnBoundaryEnterLeftBin():
  """训练的线性插值边界固定应用于验证，相等值进入左箱。"""
  train = [makeRow(str(index), value, loss=index * 2, seal=True if index == 0 else None,
                   touch=True if index < 2 else None) for index, value in enumerate((0, 4, 8, 12))]
  validation = [makeRow(str(index), value, day=VALIDATION_DAY)
                for index, value in enumerate((3, 6, 9, 100))]
  rows = train + validation
  for row in rows:
    for index, field in enumerate(FEATURE_FIELDS):
      row["features"][field] += index * 10
  contexts = {"all": allKeys(rows), "first": {(TRAIN_DAY, "0")}}
  original = deepcopy((rows, contexts))
  result = study.describeFeatureCohorts(rows, contexts)
  for index, field in enumerate(FEATURE_FIELDS):
    assert result["thresholds"][field] == [3 + index * 10, 6 + index * 10, 9 + index * 10]
    details = result["cohorts"]["all"]["train"]["features"][field]
    assert details["mean"] == 6 + index * 10
    assert details["sealedMean"] == index * 10
    assert [item["count"] for item in details["bins"]] == [1, 1, 1, 1]
    bins = result["cohorts"]["all"]["validation"]["features"][field]["bins"]
    assert [item["count"] for item in bins] == [1, 1, 1, 1]
  assert result["cohorts"]["first"]["train"]["selectedCount"] == 1
  assert result["cohorts"]["all"]["train"]["recordedSealCount"] == 1
  assert result["cohorts"]["all"]["train"]["recordedTouchCount"] == 2
  assert result["cohorts"]["all"]["train"]["unknownEventCount"] == 2
  assert (rows, contexts) == original


def testChangingValidationValuesCannotChangeAnyFittedThreshold():
  """验证期极端值不能参与训练边界拟合。"""
  rows = [makeRow("A", 0), makeRow("B", 4), makeRow("C", 8), makeRow("D", 12),
          makeRow("E", 1, day=VALIDATION_DAY)]
  contexts = {"validationOnly": {(VALIDATION_DAY, "E")}}
  before = study.describeFeatureCohorts(rows, contexts)
  rows[-1]["features"] = dict.fromkeys(FEATURE_FIELDS, -1_000_000)
  after = study.describeFeatureCohorts(rows, contexts)
  assert before["thresholds"] == after["thresholds"]
  assert after["thresholds"]["momentum"] == [3, 6, 9]


def testDailyEqualLossAndRecordedSharesPreserveMissingLabelsAndEmptyBins():
  """每日等权下行排除缺失值；已记录封板占比保留全部入箱记录作分母。"""
  rows = [makeRow("A", loss=1, seal=True, touch=True), makeRow("B", loss=3, touch=True),
          makeRow("C"), makeRow("D", day=SECOND_TRAIN_DAY, loss=10)]
  result = study.describeFeatureCohorts(rows, {"all": allKeys(rows), "empty": set()})
  group = result["cohorts"]["all"]["train"]
  assert group["selectedCount"] == 4
  assert group["validOutcomeCount"] == 3
  assert group["recordedSealCount"] == 1
  assert group["recordedTouchCount"] == group["unknownEventCount"] == 2
  assert group["meanDownsidePct"] == 6
  for field in FEATURE_FIELDS:
    assert result["thresholds"][field] == [5, 5, 5]
    details = group["features"][field]
    assert details["mean"] == details["sealedMean"] == 5
    assert details["bins"][0] == {
      "count": 4, "sealCount": 1, "touchCount": 2, "unknownEventCount": 2,
      "validOutcomeCount": 3, "meanDownsidePct": 6, "recordedSealSharePct": 25,
    }
    for empty in details["bins"][1:]:
      assert empty == {"count": 0, "sealCount": 0, "touchCount": 0, "unknownEventCount": 0,
                       "validOutcomeCount": 0, "meanDownsidePct": None, "recordedSealSharePct": None}
  for period in ("train", "validation"):
    empty = result["cohorts"]["empty"][period]
    assert empty["selectedCount"] == empty["validOutcomeCount"] == 0
    assert empty["meanDownsidePct"] is None
    assert empty["features"]["momentum"]["mean"] is None
    assert empty["features"]["momentum"]["sealedMean"] is None


def testFeatureMeanUsesRecordWeightsAndNoRecordedSealHasUnknownSealedMean():
  """特征均值按记录等权；全缺失下行和未记录封板的均值保持未知。"""
  rows = [makeRow("A", 0), makeRow("B", 0), makeRow("C", 0),
          makeRow("D", 12, day=SECOND_TRAIN_DAY)]
  group = study.describeFeatureCohorts(rows, {"all": allKeys(rows)})["cohorts"]["all"]["train"]
  assert group["features"]["momentum"]["mean"] == 3
  assert group["features"]["momentum"]["sealedMean"] is None
  assert group["meanDownsidePct"] is None
  assert group["unknownEventCount"] == 4
  assert group["features"]["momentum"]["bins"][0]["recordedSealSharePct"] == 0


def testDaysWithoutAnyValidLossDoNotAddZeroToDailyMean():
  """整日下行缺失不能被当成零风险日纳入每日均值。"""
  rows = [makeRow("A", loss=4), makeRow("B", day=SECOND_TRAIN_DAY)]
  group = study.describeFeatureCohorts(rows, {"all": allKeys(rows)})["cohorts"]["all"]["train"]
  assert group["meanDownsidePct"] == 4
  assert group["validOutcomeCount"] == 1
  assert group["features"]["momentum"]["bins"][0]["meanDownsidePct"] == 4


def testExtremeFiniteFeatureValuesKeepInterpolatedQuartilesFinite():
  """有限的正负极端数值插值时不应因相减溢出。"""
  rows = [makeRow("A", -1e308), makeRow("B", 1e308)]
  result = study.describeFeatureCohorts(rows, {"all": allKeys(rows)})
  assert result["thresholds"]["momentum"] == pytest.approx([-5e307, 0, 5e307])
  assert result["cohorts"]["all"]["train"]["features"]["momentum"]["mean"] == 0


@pytest.mark.parametrize("rows", [[], [makeRow("A", day=VALIDATION_DAY)]])
def testRejectMissingTrainingRows(rows):
  """没有训练记录时拒绝拟合，而不使用验证记录补位。"""
  with pytest.raises(ValueError, match="训练"):
    study.describeFeatureCohorts(rows, {"all": allKeys(rows)})


def testRejectDuplicateSecurityDay():
  """重复证券日不能作为多份独立观察。"""
  rows = [makeRow("A"), makeRow("A")]
  with pytest.raises(ValueError, match="重复"):
    study.describeFeatureCohorts(rows, {"all": allKeys(rows)})


@pytest.mark.parametrize("day", ["2025-09-16", "2026-06-17", "2026-10-03", "2025-02-30", None, 123])
def testRejectHoldoutAndInvalidDates(day):
  """拒绝旧留出、未来区间和非法日期。"""
  with pytest.raises(ValueError, match="开发区间"):
    study.describeFeatureCohorts([makeRow("A", day=day)], {})


@pytest.mark.parametrize("value", [None, True, False, "1", float("nan"), float("inf"), -float("inf"), 10 ** 1000])
def testRejectInvalidFeatureNumbers(value):
  """五项特征必须为有限非布尔数。"""
  row = makeRow("A")
  row["features"]["momentum"] = value
  with pytest.raises(ValueError, match="特征"):
    study.describeFeatureCohorts([row], {})


@pytest.mark.parametrize("value", [-1, True, False, "1", float("nan"), float("inf"), -float("inf")])
def testRejectInvalidDownsideNumbers(value):
  """下行只能为非负有限非布尔数或未知。"""
  with pytest.raises(ValueError, match="下行"):
    study.describeFeatureCohorts([makeRow("A", loss=value)], {})


@pytest.mark.parametrize("field", ["sealEvent", "touchEvent"])
@pytest.mark.parametrize("value", [False, 0, 1, "True", float("nan")])
def testRejectFabricatedNegativeAndNumericEventLabels(field, value):
  """正标签只接受 True，未记录只接受 None。"""
  row = makeRow("A", touch=True)
  row[field] = value
  with pytest.raises(ValueError, match="事件标签"):
    study.describeFeatureCohorts([row], {})


def testRejectSealWithoutRecordedTouch():
  """已封板正标签必须同时有触板正标签。"""
  with pytest.raises(ValueError, match="封板.*触板"):
    study.describeFeatureCohorts([makeRow("A", seal=True)], {})


@pytest.mark.parametrize("context", [
  {"all": {(TRAIN_DAY, "unknown")}}, {"all": {(VALIDATION_DAY, "A")}},
  {"all": [(TRAIN_DAY, "A")]}, {"all": {TRAIN_DAY}}, {"all": {(TRAIN_DAY,)}},
  {"all": {(TRAIN_DAY, "A", "extra")}}, {"": set()}, {1: set()}, [],
])
def testRejectUnknownAndMalformedContextKeys(context):
  """上下文必须由已存在证券日组成，禁止未知引用和畸形键。"""
  with pytest.raises(ValueError, match="上下文"):
    study.describeFeatureCohorts([makeRow("A")], context)


@pytest.mark.parametrize("problem", ["missingRowField", "missingFeature", "extraFeature", "emptyCode", "nonStringCode",
                                    "nonDictionaryRow", "nonDictionaryFeatures", "nonListRows"])
def testRejectMalformedRows(problem):
  """公开接口对行结构及固定特征集合给出明确中文异常。"""
  row = makeRow("A")
  rows = [row]
  if problem == "missingRowField":
    del row["downsidePct"]
  elif problem == "missingFeature":
    del row["features"]["momentum"]
  elif problem == "extraFeature":
    row["features"]["futureOutcome"] = 100
  elif problem == "emptyCode":
    row["code"] = ""
  elif problem == "nonStringCode":
    row["code"] = 123
  elif problem == "nonDictionaryRow":
    rows = [None]
  elif problem == "nonDictionaryFeatures":
    row["features"] = []
  else:
    rows = (row,)
  with pytest.raises(ValueError, match="记录|特征|代码"):
    study.describeFeatureCohorts(rows, {})
