"""固定盘中均值风险与趋势的局部权重比较，不扩展研究范围。"""

# 项目研究模块采用小驼峰命名。
# ruff: noqa: N999

import json
from copy import deepcopy

from quant_forge.research.intradayRiskStudy import attachRiskVariant
from quant_forge.research.observationEvaluation import EQUAL_WEIGHTS
from quant_forge.research.sealHitStudy import checkSealGate, evaluateSealModel
from quant_forge.research.weightRiskDiagnosis import developmentPeriod

VARIANT = "intradayMean20"
LOCAL_GATES = ("ALL", "ABC")
PERIODS = ("train", "validation")
RISK_PERCENTAGES = (25, 30, 35, 40, 45, 50)
PERCENT_SCALE = 100
RISK_FIELDS = ("meanDownsidePct", "tailDownsidePct")


def localWeightGrid():
  """返回事先固定的六组权重，位置与流动性始终为零。"""
  return [(risk / PERCENT_SCALE, (PERCENT_SCALE - risk) / PERCENT_SCALE, 0, 0) for risk in RISK_PERCENTAGES]


def _parts(days):
  """在评价前拒绝旧留出、未来和非法日期。"""
  parts = {period: {} for period in PERIODS}
  for day, item in days.items():
    parts[developmentPeriod(day)][day] = item
  return parts


def _canonical(value):
  """统一内存元组与 JSON 数组，不接受非有限数字。"""
  return json.dumps(value, sort_keys=True, allow_nan=False)


def _endpoints(prior, baselines):
  """只复用两个端点的四组冻结结果，核对基准及门槛，原文件摘要由运行器核验。"""
  grid = localWeightGrid()
  expected = {(weights, gate) for weights in (grid[0], grid[-1]) for gate in LOCAL_GATES}
  cached = {}
  try:
    for row in prior["comparisons"]:
      if row["variant"] != VARIANT:
        continue
      key = (tuple(row["weights"]), row["gate"])
      if key not in expected:
        continue
      if key in cached:
        raise ValueError("缓存重复")
      for period in PERIODS:
        stage = row[period]
        baseline = baselines[row["gate"]][period]
        if _canonical(stage["baseline"]) != _canonical(baseline):
          raise ValueError("原等权基准不同")
        if stage["failures"] != checkSealGate(stage["candidate"], baseline):
          raise ValueError("门槛判定不同")
      if row["eligible"] != all(not row[p]["failures"] for p in PERIODS):
        raise ValueError("合格状态不同")
      cached[key] = row
    if set(cached) != expected:
      raise ValueError("缓存缺少固定配置")
  except (KeyError, TypeError, ValueError) as error:
    raise ValueError(f"旧端点结果核验失败：{error}") from error
  return cached


def compareLocalRiskWeights(days, featureRows, priorResult):
  """比较十二个局部配置，复用四个旧端点，仅新增八个配置。

  参数 days、featureRows 为原开发候选和冻结风险附表，priorResult 为已核对来源的前版结果。
  基准始终为原风险项的四项等权名单，全部门槛不变。不修改输入。
  非法日期、附表缺口或不一致端点抛出中文 ValueError；返回逐组结果及开发候选或 None。
  """
  original = _parts(days)
  baselines = {gate: {period: evaluateSealModel(part, EQUAL_WEIGHTS, gate)
                      for period, part in original.items()} for gate in LOCAL_GATES}
  endpoints = _endpoints(priorResult, baselines)
  parts = _parts(attachRiskVariant(days, featureRows, VARIANT))
  comparisons = []
  for weights in localWeightGrid():
    for gate in LOCAL_GATES:
      endpoint = endpoints.get((weights, gate))
      row = {"id": "local-mean-w" + "-".join(str(round(w * PERCENT_SCALE)) for w in weights) + "_" + gate,
             "variant": VARIANT, "weights": weights, "gate": gate,
             "reusedEndpoint": endpoint is not None, "sourceId": endpoint["id"] if endpoint else None}
      for period, part in parts.items():
        candidate = deepcopy(endpoint[period]["candidate"]) if endpoint else evaluateSealModel(part, weights, gate)
        baseline = baselines[gate][period]
        row[period] = {"candidate": candidate, "baseline": baseline,
                       "failures": checkSealGate(candidate, baseline)}
      row["eligible"] = all(not row[p]["failures"] for p in PERIODS)
      comparisons.append(row)
  eligible = [r for r in comparisons if r["eligible"]]
  selected = min(eligible, key=lambda r: (
    r["validation"]["candidate"]["tailDownsidePct"], r["validation"]["candidate"]["meanDownsidePct"],
    -r["validation"]["candidate"]["meanUpsidePct"], r["id"],
  )) if eligible else None
  improved = bool(selected and any(selected[p]["candidate"][field] < selected[p]["baseline"][field]
                                  for p in PERIODS for field in RISK_FIELDS))
  return {"status": "development_candidate_selected" if selected else "no_eligible_development_model",
          "selected": selected, "comparisons": comparisons, "baselines": baselines,
          "riskImprovedAgainstOriginalBaseline": improved, "baselineType": "ORIGINAL_V3_EQUAL_WEIGHT",
          "metricType": "RECORDED_SEAL_EVENT_PROXY", "oldHoldoutUsed": False,
          "freshHoldoutStatus": "not_evaluated", "productionReady": False,
          "newConfigurations": len(comparisons) - len(endpoints), "reusedConfigurations": len(endpoints)}
