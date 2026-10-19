"""替代风险项必须保留候选全集、旧基准和已确认门槛。"""

from copy import deepcopy
from datetime import date, timedelta

import pytest
from test_observationStudy import daysFixture

from quant_forge.research import intradayRiskStudy as study
from quant_forge.research.observationStudy import rankCandidates
from quant_forge.research.openWeightStudy import selectOpenWeightModel


def studyFixture():
  """原低风险排序与新盘中风险排序相反，所有标签均充足。"""
  days = daysFixture()
  features = []
  for day, item in days.items():
    for row in item["rows"]:
      row["outcome"]["sealEvent"] = True
      value = 20 - int(row["code"].split(".")[0])
      row["features"]["momentum"] = value
      features.append({"date": day, "code": row["code"], "intradayMean20": value,
                       "intradayTail20": value + 1,
                       "historyStart": (date.fromisoformat(day) - timedelta(days=30)).isoformat(),
                       "historyEnd": (date.fromisoformat(day) - timedelta(days=1)).isoformat()})
    item["rows"] = rankCandidates(item["rows"])
  return days, features


def testAttachChangesOnlyRiskAndReversesItsRankWithoutMutatingInputs():
  days, features = studyFixture()
  before = deepcopy(days)
  attached = study.attachRiskVariant(days, features, "intradayMean20")
  day = next(iter(days))
  for original, new in zip(days[day]["rows"], attached[day]["rows"]):
    assert new["code"] == original["code"] and new["outcome"] == original["outcome"]
    assert new["ranks"][1:] == original["ranks"][1:]
    assert new["ranks"][0] == pytest.approx(1 - original["ranks"][0])
  assert days == before


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "future", "invalid", "nan", "negative"])
def testFeaturePanelCannotChangeUniverseOrUseFuture(change):
  days, features = studyFixture()
  if change == "missing":
    features.pop()
  elif change == "extra":
    features.append({**features[0], "code": "999999.SZ"})
  elif change == "duplicate":
    features.append(deepcopy(features[0]))
  elif change == "future":
    features[0]["historyEnd"] = features[0]["date"]
  elif change == "invalid":
    features[0]["historyStart"] = "坏日期"
  else:
    features[0]["intradayMean20"] = float("nan") if change == "nan" else -1
  with pytest.raises(ValueError, match="风险附表"):
    study.attachRiskVariant(days, features, "intradayMean20")


@pytest.mark.parametrize("day", ["2026-06-17", "2026-10-03", "错误", None])
def testOldHoldoutAndFutureDaysRejected(day):
  with pytest.raises(ValueError, match="开发区间"):
    study.attachRiskVariant({day: {"rows": [], "grade": "A"}}, [], "intradayMean20")


def testUnknownVariantRejected():
  with pytest.raises(ValueError, match="版本"):
    study.attachRiskVariant({}, [], "wrong")


def test210ConfigurationsUseOldBaselineAndReuse90ZeroRiskReferences(monkeypatch):
  days, features = studyFixture()
  prior = selectOpenWeightModel(days)
  original = deepcopy(prior)
  calls = []
  evaluator = study.evaluateSealModel

  def counted(part, weights, gate):
    calls.append((tuple(weights), gate))
    return evaluator(part, weights, gate)

  monkeypatch.setattr(study, "evaluateSealModel", counted)
  result = study.compareIntradayRisk(days, features, prior)
  assert len(result["comparisons"]) == 210
  assert result["newConfigurations"] == 120 and result["reusedConfigurations"] == 90
  assert len(calls) == 246  # 六个旧基准，120 个新配置各两段。
  for row in result["comparisons"]:
    assert row["train"]["baseline"] == prior["baselines"][row["gate"]]["train"]
    assert row["eligible"] == all(not row[p]["failures"] for p in ("train", "validation"))
    assert row["reusedOldSelection"] == (row["weights"][0] == 0)
  newEqual = next(r for r in result["comparisons"] if r["weights"] == (0.25,) * 4)
  assert newEqual["isEqualWeight"] and not newEqual["isOriginalBaseline"]
  assert newEqual["train"]["candidate"] != newEqual["train"]["baseline"]
  assert prior == original and result["oldHoldoutUsed"] is False


@pytest.mark.parametrize("change", ["count", "baseline", "eligible"])
def testCorruptOldComparisonRejected(change):
  days, features = studyFixture()
  prior = selectOpenWeightModel(days)
  if change == "count":
    prior["comparisons"].pop()
  elif change == "baseline":
    prior["comparisons"][0]["train"]["baseline"]["recordedSealCount"] = 0
  else:
    prior["comparisons"][0]["eligible"] = not prior["comparisons"][0]["eligible"]
  with pytest.raises(ValueError, match="旧结果"):
    study.compareIntradayRisk(days, features, prior)
