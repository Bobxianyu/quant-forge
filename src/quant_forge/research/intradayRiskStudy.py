"""以固定旧等权基准比较两个盘中风险评分，拒绝移动候选范围或门槛。"""

# 项目研究模块采用小驼峰命名。
# ruff: noqa: N999

import json
import math
from copy import deepcopy
from datetime import date

from quant_forge.research.intradayRiskFeatures import RISK_VARIANTS
from quant_forge.research.observationEvaluation import EQUAL_WEIGHTS, GATES
from quant_forge.research.observationStudy import rankCandidates
from quant_forge.research.openWeightStudy import openWeightGrid
from quant_forge.research.sealHitStudy import checkSealGate, evaluateSealModel
from quant_forge.research.weightRiskDiagnosis import developmentPeriod

PERIODS = ("train", "validation")
RISK_FIELDS = ("meanDownsidePct", "tailDownsidePct")


def _indexFeatures(days, featureRows):
  """严格一对一附表，未知价格不变成零，不用缺数据删除候选。"""
  expected = set()
  for day, item in days.items():
    developmentPeriod(day)
    for row in item["rows"]:
      key = (day, row["code"])
      if key in expected:
        raise ValueError("风险附表对应的原候选存在重复")
      expected.add(key)
  indexed = {}
  try:
    for row in featureRows:
      key = (row["date"], row["code"])
      if key in indexed or key not in expected:
        raise ValueError("风险附表重复或包含额外候选")
      start, end = row["historyStart"], row["historyEnd"]
      for day in (start, end):
        if date.fromisoformat(day).isoformat() != day:
          raise ValueError("历史日期非法")
      if not start <= end < row["date"]:
        raise ValueError("窗口包含未来日期")
      for variant in RISK_VARIANTS:
        value = row[variant]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value < 100):
          raise ValueError("风险值非法或缺失")
      indexed[key] = row
    if set(indexed) != expected:
      raise ValueError("缺少原候选风险值")
  except (KeyError, TypeError, ValueError) as error:
    raise ValueError(f"风险附表核验失败：{error}") from error
  return indexed


def attachRiskVariant(days, featureRows, variant):
  """返回仅替换风险项并重排风险百分位的面板，不修改原特征、标签和候选。

  附表要求严格一对一且窗口早于观察日；错误、缺失、额外键或非法版本抛出中文 ValueError。
  日期不属于固定开发区间时拒绝。其他三项排名须完全不变，以证明零风险权重选择等价。
  """
  if variant not in RISK_VARIANTS:
    raise ValueError("未知盘中风险版本")
  indexed = _indexFeatures(days, featureRows)
  attached = {}
  for day, item in days.items():
    rows = [{**row, "features": {**row["features"], "downside": indexed[(day, row["code"])][variant]}}
            for row in item["rows"]]
    ranked = rankCandidates(rows)
    if any(before["ranks"][1:] != after["ranks"][1:] for before, after in zip(item["rows"], ranked)):
      raise ValueError("风险附表替换改变了其他三项排名，零风险权重不能复用")
    attached[day] = {**item, "rows": ranked}
  return attached


def _canonical(value):
  """JSON 和内存中元组、数组统一比较，拒绝非有限数据。"""
  return json.dumps(value, sort_keys=True, allow_nan=False)


def _oldComparisons(priorResult, baselines):
  """旧文件来源由运行器核验；此处核验网格完整性、旧配对基准与判定。"""
  expected = {(weights, gate) for weights in openWeightGrid() for gate in GATES}
  cached = {}
  try:
    for row in priorResult["comparisons"]:
      key = (tuple(row["weights"]), row["gate"])
      if key not in expected or key in cached:
        raise ValueError("组合重复或越界")
      for period in PERIODS:
        stage = row[period]
        baseline = baselines[row["gate"]][period]
        if _canonical(stage["baseline"]) != _canonical(baseline):
          raise ValueError("原基准不同")
        if checkSealGate(stage["candidate"], baseline) != stage["failures"]:
          raise ValueError("门槛摘要不同")
      if row["eligible"] != all(not row[p]["failures"] for p in PERIODS):
        raise ValueError("合格状态不同")
      cached[key] = row
    if set(cached) != expected:
      raise ValueError("原网格不完整")
  except (TypeError, KeyError, ValueError) as error:
    raise ValueError(f"旧结果核验失败：{error}") from error
  return cached


def _partitions(days):
  """只允许训练与验证标签进入比较。"""
  parts = {period: {} for period in PERIODS}
  for day, item in days.items():
    parts[developmentPeriod(day)][day] = item
  return parts


def compareIntradayRisk(days, featureRows, priorResult):
  """比较固定两个风险版本的 210 个配置，门槛始终使用旧等权名单。

  days 和附表必须覆盖同一开发候选全集，priorResult 是已验证来源的第三版结果。
  返回全部候选、旧同权重对照、固定基准及开发选择；违规输入抛出中文 ValueError。
  """
  original = _partitions(days)
  baselines = {gate: {period: evaluateSealModel(part, EQUAL_WEIGHTS, gate) for period, part in original.items()}
               for gate in GATES}
  cached = _oldComparisons(priorResult, baselines)
  comparisons = []
  for variant in RISK_VARIANTS:
    parts = _partitions(attachRiskVariant(days, featureRows, variant))
    for weights in openWeightGrid():
      for gate in GATES:
        old = cached[(weights, gate)]
        reused = weights[0] == 0
        row = {"id": variant + "-w" + "-".join(str(round(w * 100)) for w in weights) + "_" + gate,
               "variant": variant, "weights": weights, "gate": gate, "isEqualWeight": weights == EQUAL_WEIGHTS,
               "isOriginalBaseline": False, "reusedOldSelection": reused, "oldSameWeightId": old["id"]}
        for period, part in parts.items():
          candidate = deepcopy(old[period]["candidate"]) if reused else evaluateSealModel(part, weights, gate)
          baseline = baselines[gate][period]
          row[period] = {"candidate": candidate, "baseline": baseline,
                         "oldSameWeight": deepcopy(old[period]["candidate"]),
                         "failures": checkSealGate(candidate, baseline)}
        row["eligible"] = all(not row[p]["failures"] for p in PERIODS)
        comparisons.append(row)
  eligible = [row for row in comparisons if row["eligible"]]
  selected = min(eligible, key=lambda r: (
    r["validation"]["candidate"]["tailDownsidePct"], r["validation"]["candidate"]["meanDownsidePct"],
    -r["validation"]["candidate"]["meanUpsidePct"], r["id"],
  )) if eligible else None
  improved = bool(selected and any(selected[p]["candidate"][f] < selected[p]["baseline"][f]
                                  for p in PERIODS for f in RISK_FIELDS))
  return {"status": "development_candidate_selected" if selected else "no_eligible_development_model",
          "selected": selected, "riskImprovedAgainstOriginalBaseline": improved,
          "comparisons": comparisons, "baselines": baselines,
          "metricType": "RECORDED_SEAL_EVENT_PROXY", "baselineType": "ORIGINAL_V3_EQUAL_WEIGHT",
          "newConfigurations": sum(not r["reusedOldSelection"] for r in comparisons),
          "reusedConfigurations": sum(r["reusedOldSelection"] for r in comparisons),
          "oldHoldoutUsed": False, "freshHoldoutStatus": "not_evaluated", "productionReady": False}
