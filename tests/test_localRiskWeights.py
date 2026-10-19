"""局部研究不得扩展网格、移动基准或读取旧留出。"""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from test_intradayRiskStudy import studyFixture

from quant_forge.research import localRiskWeights as study
from quant_forge.research.intradayRiskStudy import compareIntradayRisk
from quant_forge.research.openWeightStudy import selectOpenWeightModel


@pytest.fixture(scope="module")
def sourceFixture():
  """生成固定旧版本结果供缓存与固定基准测试。"""
  days, features = studyFixture()
  prior = compareIntradayRisk(days, features, selectOpenWeightModel(days))
  return days, features, prior


def endpoint(prior):
  """明确找到本轮需复用的端点，避免碰到其他不相关配置。"""
  return next(r for r in prior["comparisons"] if r["variant"] == "intradayMean20"
              and r["weights"] == (0.25, 0.75, 0, 0) and r["gate"] == "ALL")


def testGridIsExactlySixOneDimensionalPoints():
  grid = study.localWeightGrid()
  assert len(grid) == len(set(grid)) == 6
  assert [round(w[0] * 100) for w in grid] == [25, 30, 35, 40, 45, 50]
  assert all(sum(w) == pytest.approx(1) and w[2:] == (0, 0) for w in grid)


def testTwelveRowsReuseFourEndpointsAndKeepOriginalBaseline(sourceFixture, monkeypatch):
  days, features, prior = sourceFixture
  before = deepcopy(prior)
  calls = []
  evaluate = study.evaluateSealModel

  def counted(part, weights, gate):
    calls.append((tuple(weights), gate))
    return evaluate(part, weights, gate)

  monkeypatch.setattr(study, "evaluateSealModel", counted)
  result = study.compareLocalRiskWeights(days, features, prior)
  assert len(result["comparisons"]) == 12
  assert result["newConfigurations"] == 8 and result["reusedConfigurations"] == 4
  assert len(calls) == 20  # 四份旧基准，八个新增配置各两段。
  assert {r["gate"] for r in result["comparisons"]} == {"ALL", "ABC"}
  for row in result["comparisons"]:
    assert row["eligible"] == all(not row[p]["failures"] for p in ("train", "validation"))
    for p in ("train", "validation"):
      assert row[p]["baseline"] == prior["baselines"][row["gate"]][p]
  assert result["oldHoldoutUsed"] is False and result["freshHoldoutStatus"] == "not_evaluated"
  assert before == prior


@pytest.mark.parametrize("damage", ["missing", "duplicate", "baseline", "failures", "eligible"])
def testBadEndpointCacheRejected(sourceFixture, damage):
  days, features, original = sourceFixture
  prior = deepcopy(original)
  row = endpoint(prior)
  if damage == "missing":
    prior["comparisons"].remove(row)
  elif damage == "duplicate":
    prior["comparisons"].append(deepcopy(row))
  elif damage == "baseline":
    row["train"]["baseline"]["recordedSealCount"] = 0
  elif damage == "failures":
    row["train"]["failures"] = ["编造失败"]
  else:
    row["eligible"] = not row["eligible"]
  with pytest.raises(ValueError, match="端点"):
    study.compareLocalRiskWeights(days, features, prior)


@pytest.mark.parametrize("day", ["2026-06-17", "2026-10-04", "坏日期", None])
def testInvalidDevelopmentDateRejected(day):
  with pytest.raises(ValueError, match="开发区间"):
    study.compareLocalRiskWeights({day: {"grade": "A", "rows": []}}, [], {})


def testMissingFeatureDoesNotShrinkUniverse(sourceFixture):
  days, features, prior = sourceFixture
  with pytest.raises(ValueError, match="风险附表"):
    study.compareLocalRiskWeights(days, features[:-1], prior)


def testNoPassDoesNotRelaxGates(sourceFixture):
  days, features, _ = deepcopy(sourceFixture)
  for item in days.values():
    for row in item["rows"]:
      row["outcome"]["sealEvent"] = None
  prior = compareIntradayRisk(days, features, selectOpenWeightModel(days))
  result = study.compareLocalRiskWeights(days, features, prior)
  assert result["status"] == "no_eligible_development_model" and result["selected"] is None


def testSelectionUsesFixedRiskOrderWhenEvidencePasses():
  days, features = studyFixture()
  for item in days.values():
    for row in item["rows"]:
      row["outcome"]["downsidePct"] = 1
  prior = compareIntradayRisk(days, features, selectOpenWeightModel(days))
  result = study.compareLocalRiskWeights(days, features, prior)
  assert result["status"] == "development_candidate_selected"
  assert result["selected"]["eligible"]
  assert result["riskImprovedAgainstOriginalBaseline"] is False


@pytest.mark.parametrize("damage", [None, "thirdInput", "secondInput", "panel", "firstInput"])
def testRunnerRequiresAnchoredPriorInputChain(damage):
  root = Path(__file__).resolve().parents[1]
  spec = spec_from_file_location("localRiskRunner", root / "outputs/research/local-risk-weights-20261004/runStudy.py")
  runner = module_from_spec(spec)
  spec.loader.exec_module(runner)
  panel = {"panelSha256": "panel", "inputSha256": "first"}
  prior = {"thirdInputSha256": "third"}
  older = {
    "open-weight-study-20260928": {"sha256": "third", "identity": {
      "priorInputSha256": "second", "firstPanelSha256": "panel", "firstInputSha256": "first"}},
    "seal-hit-study-20260928": {"sha256": "second", "identity": {
      "firstPanelSha256": "panel", "firstInputSha256": "first"}},
  }
  third = older["open-weight-study-20260928"]
  if damage == "thirdInput":
    third["sha256"] = "other"
  elif damage == "secondInput":
    third["identity"]["priorInputSha256"] = "other"
  elif damage in ("panel", "firstInput"):
    key = "firstPanelSha256" if damage == "panel" else "firstInputSha256"
    older["seal-hit-study-20260928"]["identity"][key] = "other"
  if damage:
    with pytest.raises(ValueError, match="来源链"):
      runner.verifyPriorBindings(prior, older, panel)
  else:
    assert runner.verifyPriorBindings(prior, older, panel) is None
