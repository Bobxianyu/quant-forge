"""风险优先的独立观察比较；选择器拒绝任何最终留出日期。"""

# 项目研究模块采用小驼峰命名。
# ruff: noqa: N999

import math
from statistics import mean

from quant_forge.research.observationStudy import DEFAULT_TOP, selectCandidates, validateWeights, weightGrid

EQUAL_WEIGHTS = (0.25, 0.25, 0.25, 0.25)
LIQUIDITY_WEIGHTS = (0.0, 0.0, 0.0, 1.0)
GATES = {"ALL": "ABCD", "ABC": "ABC", "AB": "AB"}
PERIODS = {"train": ("2025-09-17", "2026-03-16"),
           "validation": ("2026-03-17", "2026-06-16"),
           "holdout": ("2026-06-17", "2026-09-16")}
TAIL_FRACTION = 0.1
MIN_OBSERVED_DAYS = 20
MIN_ACTIVE_FRACTION = 0.4
MIN_OUTCOME_COVERAGE = 99.0


def splitName(day):
  """按冻结日期返回训练、验证或留出名称，区间外抛出 ValueError。"""
  for name, (start, end) in PERIODS.items():
    if start <= day <= end:
      return name
  raise ValueError(f"日期不在冻结研究区间：{day}")


def evaluateModel(days, weights, gate, *, allCandidates=False, includeDaily=False):
  """汇总指定日期集合的候选观察，空推荐不计零损失，保留缺失分母。

  参数：days 为日期到 grade/rows 的映射，rows 已预先按同日特征计算 ranks。
  返回：每日等权的统计字典；完整触板率与封板率因标签不完整始终为 None。
  异常：非法权重或环境模式抛出 ValueError，不拟合任何输入。
  """
  validateWeights(weights)
  if gate not in GATES:
    raise ValueError("未知环境观察模式")
  counters = dict.fromkeys(("selectedCount", "validOutcomeCount", "recordedTouchCount", "recordedSealCount",
                           "blockedDays", "unknownEnvironmentDays", "insufficientCandidateDays"), 0)
  daily = []
  worst = None
  for day, item in sorted(days.items()):
    if item["grade"] is None:
      counters["unknownEnvironmentDays"] += 1
      continue
    if item["grade"] not in GATES[gate]:
      counters["blockedDays"] += 1
      continue
    rows = item["rows"] if allCandidates else selectCandidates(item["rows"], weights)
    if len(rows) < DEFAULT_TOP:
      counters["insufficientCandidateDays"] += 1
      continue
    counters["selectedCount"] += len(rows)
    outcomes = [row["outcome"] for row in rows]
    valid = [row for row in outcomes if row["downsidePct"] is not None]
    counters["validOutcomeCount"] += len(valid)
    counters["recordedTouchCount"] += sum(row["touchEvent"] is True for row in outcomes)
    counters["recordedSealCount"] += sum(row["sealEvent"] is True for row in outcomes)
    observed = {"date": day, "selectedCount": len(rows), "validOutcomeCount": len(valid),
                "codes": [row["code"] for row in rows]}
    for field in ("downsidePct", "upsidePct", "closePct"):
      observed[field] = mean(row[field] for row in valid) if valid else None
    if valid:
      worst = max(worst or 0, max(row["downsidePct"] for row in valid))
    daily.append(observed)
  usable = [row for row in daily if row["downsidePct"] is not None]
  losses = sorted((row["downsidePct"] for row in usable), reverse=True)
  tailCount = math.ceil(len(losses) * TAIL_FRACTION)
  selected = counters["selectedCount"]
  result = {**counters, "dateCount": len(days), "observedDays": len(usable), "recommendedDays": len(daily),
            "environmentValidDays": len(days) - counters["unknownEnvironmentDays"],
            "missingOutcomeCount": selected - counters["validOutcomeCount"],
            "historicalEligibilityUnknownCount": selected,
            "outcomeCoveragePct": counters["validOutcomeCount"] / selected * 100 if selected else None,
            "meanDownsidePct": mean(losses) if losses else None,
            "tailDownsidePct": mean(losses[:tailCount]) if losses else None,
            "meanUpsidePct": mean(row["upsidePct"] for row in usable) if usable else None,
            "meanClosePct": mean(row["closePct"] for row in usable) if usable else None,
            "worstCandidateDownsidePct": worst, "trueTouchRatePct": None, "trueSealRatePct": None,
            "recordedTouchLowerBoundPct": counters["recordedTouchCount"] / selected * 100 if selected else None,
            "recordedSealLowerBoundPct": counters["recordedSealCount"] / selected * 100 if selected else None}
  if includeDaily:
    result["daily"] = daily
  return result


def _enough(metrics):
  """最低天数、输出占比和结果覆盖同时满足才允许比较，不能靠全空仓胜出。"""
  return (metrics["observedDays"] >= MIN_OBSERVED_DAYS
          and metrics["recommendedDays"] >= metrics["environmentValidDays"] * MIN_ACTIVE_FRACTION
          and metrics["outcomeCoveragePct"] is not None
          and metrics["outcomeCoveragePct"] >= MIN_OUTCOME_COVERAGE)


def chooseModel(days):
  """训练做风险约束、验证选一次组合；传入留出日期立即拒绝，未通过不降低门槛。"""
  partitions = {"train": {}, "validation": {}}
  for day, item in days.items():
    split = splitName(day)
    if split == "holdout":
      raise ValueError("权重选择器禁止读取最终留出区间")
    partitions[split][day] = item
  baseline = {name: evaluateModel(part, EQUAL_WEIGHTS, "ALL") for name, part in partitions.items()}
  comparisons = []
  for weights in weightGrid():
    for gate in GATES:
      metrics = {name: evaluateModel(part, weights, gate) for name, part in partitions.items()}
      eligible = all(_enough(metrics[name]) and _enough(baseline[name]) for name in partitions)
      if eligible:
        eligible = all(metrics[name]["tailDownsidePct"] <= baseline[name]["tailDownsidePct"] for name in partitions)
        eligible = eligible and metrics["validation"]["meanDownsidePct"] <= baseline["validation"]["meanDownsidePct"]
      identifier = "w" + "-".join(str(round(w * 100)) for w in weights) + "_" + gate
      comparisons.append({"id": identifier, "weights": weights, "gate": gate, "eligible": eligible, **metrics})
  eligibleModels = [row for row in comparisons if row["eligible"]]
  selected = min(eligibleModels, key=lambda row: (
    row["validation"]["tailDownsidePct"], row["validation"]["meanDownsidePct"],
    -row["validation"]["meanUpsidePct"], row["id"],
  )) if eligibleModels else None
  return {"status": "selected" if selected else "no_eligible_model", "selected": selected,
          "baseline": baseline, "comparisons": comparisons}
