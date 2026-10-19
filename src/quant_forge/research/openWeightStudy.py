"""第三版开放完整权重网格，保留封板、实测风险及时间隔离约束。"""

# 项目研究模块采用小驼峰文件名。
# ruff: noqa: N999

import json
from copy import deepcopy
from datetime import date

from quant_forge.research.observationEvaluation import EQUAL_WEIGHTS, GATES, PERIODS
from quant_forge.research.observationStudy import weightGrid
from quant_forge.research.sealHitStudy import checkSealGate, evaluateSealModel

GRID_UNITS = 4
DEVELOPMENT_PERIODS = ("train", "validation")


def openWeightGrid():
  """返回四项非负、合计一的完整四分之一网格；允许各项权重为零。"""
  return [(risk / GRID_UNITS, trend / GRID_UNITS, position / GRID_UNITS,
           (GRID_UNITS - risk - trend - position) / GRID_UNITS)
          for risk in range(GRID_UNITS + 1)
          for trend in range(GRID_UNITS - risk + 1)
          for position in range(GRID_UNITS - risk - trend + 1)]


def _developmentParts(days):
  """先拒绝旧留出、未来和无效日期，再分割固定开发区间。"""
  parts = {period: {} for period in DEVELOPMENT_PERIODS}
  for day, item in days.items():
    try:
      validDay = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
    except ValueError:
      validDay = False
    period = next((name for name in parts if validDay and PERIODS[name][0] <= day <= PERIODS[name][1]), None)
    if period is None:
      raise ValueError(f"开放权重研究只允许固定开发区间，不能读取旧留出或未来标签：{day}")
    parts[period][day] = item
  return parts


def _canonical(value):
  """使 JSON 数组与内存元组具有相同的核验表示，拒绝非有限数字。"""
  return json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=False)


def _validatedCache(priorResult, baselines):
  """检查旧三十组结构、配对基准和门槛；文件来源摘要由调用方预先核验。"""
  if priorResult is None:
    return {}
  expected = {(weights, gate) for weights in weightGrid() for gate in GATES}
  cached = {}
  try:
    for row in priorResult["comparisons"]:
      key = (tuple(row["weights"]), row["gate"])
      if key not in expected or key in cached:
        raise ValueError("权重或环境重复、缺失或越界")
      for period in DEVELOPMENT_PERIODS:
        stage = row[period]
        baseline = baselines[row["gate"]][period]
        if _canonical(stage["baseline"]) != _canonical(baseline):
          raise ValueError("配对基准不同")
        if stage["failures"] != checkSealGate(stage["candidate"], baseline):
          raise ValueError("门槛判定不同")
      if row["eligible"] != all(not row[p]["failures"] for p in DEVELOPMENT_PERIODS):
        raise ValueError("合格状态不同")
      cached[key] = row
    if set(cached) != expected:
      raise ValueError("组合数量或范围不同")
  except (KeyError, TypeError, ValueError) as error:
    raise ValueError(f"第二版缓存核验失败：{error}") from error
  return cached


def _selectionOrder(row):
  """先比较验证风险和空间；指标完全相同时优先保留等权对照。"""
  metrics = row["validation"]["candidate"]
  return (metrics["tailDownsidePct"], metrics["meanDownsidePct"], -metrics["meanUpsidePct"],
          not row["isEqualWeight"], row["id"])


def selectOpenWeightModel(days, priorResult=None):
  """返回一百零五组开发比较，不读取旧留出、不将等权对照标记成优化。

  参数 days 为固定开发区间面板；可选 priorResult 必须来自来源摘要已验证的第二版产物。
  非法日期或不一致缓存抛出中文 ValueError。不修改输入，风险改善仅指配对验证尾部风险严格降低。
  """
  parts = _developmentParts(days)
  baselines = {gate: {period: evaluateSealModel(part, EQUAL_WEIGHTS, gate) for period, part in parts.items()}
               for gate in GATES}
  cached = _validatedCache(priorResult, baselines)
  comparisons = []
  for weights in openWeightGrid():
    for gate in GATES:
      prior = cached.get((weights, gate))
      row = {"id": "seal-v3-w" + "-".join(str(round(w * 100)) for w in weights) + "_" + gate,
             "weights": weights, "gate": gate, "isEqualWeight": weights == EQUAL_WEIGHTS}
      for period, part in parts.items():
        candidate = deepcopy(prior[period]["candidate"]) if prior else evaluateSealModel(part, weights, gate)
        baseline = baselines[gate][period]
        row[period] = {"candidate": candidate, "baseline": baseline,
                       "failures": checkSealGate(candidate, baseline)}
      row["eligible"] = all(not row[p]["failures"] for p in parts)
      comparisons.append(row)
  eligible = [row for row in comparisons if row["eligible"]]
  selected = min(eligible, key=_selectionOrder) if eligible else None
  status = "no_eligible_development_model"
  if selected:
    status = "baseline_retained" if selected["isEqualWeight"] else "development_candidate_selected"
  improved = bool(selected and selected["validation"]["candidate"]["tailDownsidePct"]
                  < selected["validation"]["baseline"]["tailDownsidePct"])
  return {"status": status, "metricType": "RECORDED_SEAL_EVENT_PROXY", "oldHoldoutUsed": False,
          "freshHoldoutStatus": "not_evaluated", "selected": selected, "comparisons": comparisons,
          "baselines": baselines, "reusedCombinations": len(cached), "newCombinations": len(comparisons) - len(cached),
          "riskImprovedAgainstPairedBaseline": improved}
