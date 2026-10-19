"""第二版研究：先满足同条件基准的已记录封板比例，再比较下行风险。

复用第一版不可变特征和面板，不读取旧留出标签，不代表正式推荐或真实完整封板率。
"""

# 项目研究模块采用小驼峰文件名。
# ruff: noqa: N999

import math
from datetime import date

from quant_forge.research.observationEvaluation import EQUAL_WEIGHTS, GATES, PERIODS, evaluateModel
from quant_forge.research.observationStudy import weightGrid

MIN_OBSERVED_DAYS = 30
MIN_OBSERVATIONS = 300
MIN_SEAL_EVENTS = 10
MIN_SEAL_DAYS = 5
MIN_OUTCOME_COVERAGE = 99.0
MIN_ACTIVE_FRACTION = 0.4
RISK_FIELDS = ("meanDownsidePct", "tailDownsidePct")
COUNT_FIELDS = ("observedDays", "recommendedDays", "environmentValidDays", "selectedCount",
                "validOutcomeCount", "recordedSealCount", "recordedSealDays")


def _finiteNonNegative(value):
  """拒绝布尔、未知、非有限或负数，不把缺指标当成零风险。"""
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return False
  try:
    return math.isfinite(value) and value >= 0
  except OverflowError:
    return False


def _sampleFailures(metrics):
  """检查样本和事件数量，基准也要接受同样检查。"""
  if any(type(metrics.get(field)) is not int or metrics[field] < 0 for field in COUNT_FIELDS):
    return ["INVALID_COUNTS"]
  failures = []
  if metrics["observedDays"] < MIN_OBSERVED_DAYS:
    failures.append("INSUFFICIENT_OBSERVED_DAYS")
  if metrics["selectedCount"] < MIN_OBSERVATIONS:
    failures.append("INSUFFICIENT_OBSERVATIONS")
  if metrics["recommendedDays"] < metrics["environmentValidDays"] * MIN_ACTIVE_FRACTION:
    failures.append("INSUFFICIENT_ACTIVE_DAYS")
  coverage = metrics.get("outcomeCoveragePct")
  if not _finiteNonNegative(coverage) or not MIN_OUTCOME_COVERAGE <= coverage <= 100:
    failures.append("INSUFFICIENT_OUTCOME_COVERAGE")
  if metrics["recordedSealCount"] < MIN_SEAL_EVENTS:
    failures.append("INSUFFICIENT_SEAL_EVENTS")
  if metrics["recordedSealDays"] < MIN_SEAL_DAYS:
    failures.append("CONCENTRATED_SEAL_EVENTS")
  if (metrics["recordedSealCount"] > metrics["selectedCount"]
      or metrics["validOutcomeCount"] > metrics["selectedCount"]
      or metrics["recordedSealDays"] > metrics["recommendedDays"]):
    failures.append("INVALID_COUNTS")
  return failures


def _shape(metrics):
  """规范 JSON/内存两种每日数量表示，拒绝日期重复、无效日期和无效数量。"""
  try:
    shape = [(day, count) for day, count in metrics["selectionShape"]]
    if any(date.fromisoformat(day).isoformat() != day or type(count) is not int or count <= 0
           for day, count in shape):
      return None
    if shape != sorted(shape) or len({day for day, _ in shape}) != len(shape):
      return None
    if sum(count for _, count in shape) != metrics["selectedCount"]:
      return None
    return shape
  except (KeyError, TypeError, ValueError):
    return None


def checkSealGate(candidate, baseline):
  """返回未达标原因列表，空列表代表研究代理指标通过，而非真实封板率或显著性证明。

  参数：candidate、baseline 是配对日期的评价摘要，含 selectionShape 及事件计数。
  非法或未知数字按未达标处理；非字典参数抛出中文 ValueError。不修改输入。
  """
  if not isinstance(candidate, dict) or not isinstance(baseline, dict):
    raise ValueError("封板门槛摘要必须为字典")  # noqa: TRY004 -- 研究校验契约统一使用 ValueError。
  failures = _sampleFailures(candidate)
  failures.extend("BASELINE_" + reason for reason in _sampleFailures(baseline))
  candidateShape, baselineShape = _shape(candidate), _shape(baseline)
  if candidateShape is None or baselineShape is None or candidateShape != baselineShape:
    failures.append("UNPAIRED_DATES_OR_COUNTS")
  counts = [metrics.get(field) for metrics in (candidate, baseline)
            for field in ("recordedSealCount", "selectedCount")]
  if all(type(value) is int and value >= 0 for value in counts):
    candidateHits, candidateN, baselineHits, baselineN = counts
    # 比较的是已记录正事件占全部选择的比例，不将池外未知标为真实负例。
    if candidateHits * baselineN < baselineHits * candidateN:
      failures.append("SEAL_RATE_BELOW_BASELINE")
  if not all(_finiteNonNegative(metrics.get(field)) for metrics in (candidate, baseline) for field in RISK_FIELDS):
    failures.append("INVALID_RISK_METRICS")
  else:
    if candidate["meanDownsidePct"] > baseline["meanDownsidePct"]:
      failures.append("MEAN_RISK_ABOVE_BASELINE")
    if candidate["tailDownsidePct"] > baseline["tailDownsidePct"]:
      failures.append("TAIL_RISK_ABOVE_BASELINE")
  return failures


def evaluateSealModel(days, weights, gate):
  """复用原观察指标，另记封板事件分布与每日分母，不让炸板计入封板。"""
  metrics = evaluateModel(days, weights, gate, includeDaily=True)
  daily = metrics.pop("daily")
  metrics["selectionShape"] = [(row["date"], row["selectedCount"]) for row in daily]
  metrics["recordedSealDays"] = 0
  for row in daily:
    selectedCodes = set(row["codes"])
    sealed = any(candidate["outcome"]["sealEvent"] is True
                 for candidate in days[row["date"]]["rows"] if candidate["code"] in selectedCodes)
    metrics["recordedSealDays"] += int(sealed)
  return metrics


def selectSealModel(days):
  """只在原开发训练/验证日期比较三十组；旧留出与未来日期全部拒绝，不通过不扩大搜索。

  返回逐组合、逐阶段的配对基准与失败原因，以及锁定的开发候选或 None。
  结果明确 freshHoldoutStatus=not_evaluated，不将开发通过称为新的样本外证明。
  """
  parts = {"train": {}, "validation": {}}
  for day, item in days.items():
    try:
      validDay = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
    except ValueError:
      validDay = False
    period = next((name for name in parts if validDay and PERIODS[name][0] <= day <= PERIODS[name][1]), None)
    if period is None:
      raise ValueError(f"封板研究只允许固定开发区间，不能读取旧留出或未来标签：{day}")
    parts[period][day] = item
  baselines = {gate: {period: evaluateSealModel(part, EQUAL_WEIGHTS, gate) for period, part in parts.items()}
               for gate in GATES}
  comparisons = []
  for weights in weightGrid():
    for gate in GATES:
      row = {"id": "seal-v2-w" + "-".join(str(round(w * 100)) for w in weights) + "_" + gate,
             "weights": weights, "gate": gate}
      for period, part in parts.items():
        candidate = evaluateSealModel(part, weights, gate)
        baseline = baselines[gate][period]
        row[period] = {"candidate": candidate, "baseline": baseline,
                       "failures": checkSealGate(candidate, baseline)}
      row["eligible"] = all(not row[period]["failures"] for period in parts)
      comparisons.append(row)
  eligible = [row for row in comparisons if row["eligible"]]
  selected = min(eligible, key=lambda row: (
    row["validation"]["candidate"]["tailDownsidePct"],
    row["validation"]["candidate"]["meanDownsidePct"],
    -row["validation"]["candidate"]["meanUpsidePct"], row["id"],
  )) if eligible else None
  return {"status": "development_candidate_selected" if selected else "no_eligible_development_model",
          "metricType": "RECORDED_SEAL_EVENT_PROXY", "freshHoldoutStatus": "not_evaluated",
          "selected": selected, "comparisons": comparisons, "baselines": baselines,
          "limits": {"minObservedDays": MIN_OBSERVED_DAYS, "minObservations": MIN_OBSERVATIONS,
                     "minSealEvents": MIN_SEAL_EVENTS, "minSealDays": MIN_SEAL_DAYS,
                     "minActiveFraction": MIN_ACTIVE_FRACTION, "minOutcomeCoveragePct": MIN_OUTCOME_COVERAGE}}
