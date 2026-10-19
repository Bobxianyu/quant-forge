"""固定模型的开发期差异诊断；只描述既有结果，不选择权重或报告显著性。"""

# 项目研究模块采用小驼峰命名。
# ruff: noqa: N999

import math
from collections import defaultdict
from collections.abc import Mapping
from statistics import mean

from quant_forge.research.weightRiskDiagnosis import developmentPeriod

PERIOD_NAMES = ("train", "validation")
GRADES = ("A", "B", "C", "D")
SIDES = ("candidate", "baseline")
PERCENT_SCALE = 100.0
COUNT_FIELDS = ("candidateSelectedCount", "baselineSelectedCount", "candidateSealCount",
                "baselineSealCount", "sharedCount")
REQUIRED_FIELDS = ("date", "grade", "candidateMeanPct", "baselineMeanPct", *COUNT_FIELDS)


def _validateRows(dailyRows):
  """校验一个模型的日记录，复制所需字段，拒绝混合重复日和不合法观测。"""
  if isinstance(dailyRows, (str, bytes, Mapping)):
    raise ValueError("每日记录必须为记录序列")  # noqa: TRY004 - 公共诊断接口统一使用 ValueError。
  try:
    source = list(dailyRows)
  except TypeError as error:
    raise ValueError("每日记录必须为记录序列") from error
  rows, seen = [], set()
  for index, item in enumerate(source):
    if not isinstance(item, Mapping) or any(field not in item for field in REQUIRED_FIELDS):
      raise ValueError(f"每日记录第 {index + 1} 行缺少必要字段")
    row = {field: item[field] for field in REQUIRED_FIELDS}
    row["period"] = developmentPeriod(row["date"])
    if row["date"] in seen:
      raise ValueError("每日记录日期重复，不能混合不同模型")
    seen.add(row["date"])
    if row["grade"] not in GRADES:
      raise ValueError(f"每日记录环境必须为 A 到 D：{row['date']}")
    _validateCounts(row)
    for side in SIDES:
      _validateRisk(row[side + "MeanPct"], row["date"])
    rows.append(row)
  return sorted(rows, key=lambda row: row["date"])


def _validateCounts(row):
  """选择数必须同数且为正，正事件数与重叠数不得超过选择数。"""
  for field in COUNT_FIELDS:
    value = row[field]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
      raise ValueError(f"每日记录计数非法：{row['date']} 的 {field}")
  selected = row["candidateSelectedCount"]
  if selected <= 0 or row["baselineSelectedCount"] != selected:
    raise ValueError(f"每日配对选择数量必须相同且大于零：{row['date']}")
  if any(row[field] > selected for field in ("candidateSealCount", "baselineSealCount", "sharedCount")):
    raise ValueError(f"每日记录事件或重叠计数超过选择数量：{row['date']}")


def _validateRisk(value, day):
  """下行风险允许缺失；拒绝布尔、负数和非有限值。"""
  if value is None:
    return
  validType = not isinstance(value, bool) and isinstance(value, (int, float))
  try:
    valid = validType and math.isfinite(value) and value >= 0
  except OverflowError:
    valid = False
  if not valid:
    raise ValueError(f"每日记录风险均值非法：{day}")


def _pairedRows(rows):
  """风险差只使用两侧结果都存在的同一天。"""
  return [row for row in rows if all(row[side + "MeanPct"] is not None for side in SIDES)]


def _meanOrNone(values):
  """均值没有有效样本时保持未知。"""
  return mean(values) if values else None


def _observationCount(rows):
  """两侧选择数量相同，观察数取一侧分母，避免重复计数。"""
  return sum(row["candidateSelectedCount"] for row in rows)


def _dailySummary(rows):
  """风险使用配对日等权，封板仅汇总已记录正事件与观察分母。"""
  paired = _pairedRows(rows)
  selected = _observationCount(rows)
  result = {"dayCount": len(rows), "pairedDays": len(paired), "unpairedDays": len(rows) - len(paired),
            "meanRiskDeltaPct": _meanOrNone([row["candidateMeanPct"] - row["baselineMeanPct"] for row in paired]),
            "meanOverlapFraction": _meanOrNone([row["sharedCount"] / row["candidateSelectedCount"] for row in rows])}
  for side in SIDES:
    seals = sum(row[side + "SealCount"] for row in rows)
    result.update({side + "MeanPct": _meanOrNone([row[side + "MeanPct"] for row in paired]),
                   side + "SelectedCount": selected, side + "SealCount": seals,
                   side + "RecordedSealLowerBoundPct": seals / selected * PERCENT_SCALE if selected else None})
  sealDifference = result["candidateSealCount"] - result["baselineSealCount"]
  result["sealDeltaPp"] = sealDifference / selected * PERCENT_SCALE if selected else None
  return result


def _groupRows(rows, key):
  """按环境或自然月稳定分组，不新增不存在的组。"""
  groups = defaultdict(list)
  for row in rows:
    groups[key(row)].append(row)
  return dict(sorted(groups.items()))


def _periodSummary(rows):
  """保留分段总体、环境与自然月的相同统计口径。"""
  grades = _groupRows(rows, lambda row: row["grade"])
  months = _groupRows(rows, lambda row: row["date"][:7])
  return {"overall": _dailySummary(rows),
          "byGrade": {key: _dailySummary(group) for key, group in grades.items()},
          "byMonth": {key: _dailySummary(group) for key, group in months.items()}}


def _supportSummary(rows, eligible, common):
  """单列共同支持之外和风险缺失的天数、观察数，不把缺失当作零风险。"""
  included = [row for row in eligible if row["grade"] in common]
  return {"totalDayCount": len(rows), "totalObservationCount": _observationCount(rows),
          "eligibleDayCount": len(eligible), "eligibleObservationCount": _observationCount(eligible),
          "commonDayCount": len(included), "commonObservationCount": _observationCount(included),
          "excludedDayCount": len(eligible) - len(included),
          "excludedObservationCount": _observationCount(eligible) - _observationCount(included),
          "missingRiskDayCount": len(rows) - len(eligible),
          "missingRiskObservationCount": _observationCount(rows) - _observationCount(eligible)}


def _decompose(parts, metric):
  """在两期共同环境支持上，对候选相对基准的增量作对称恒等分解。"""
  risk = metric == "meanRiskDeltaPct"
  eligible = {period: _pairedRows(rows) if risk else rows for period, rows in parts.items()}
  groups = {period: _groupRows(rows, lambda row: row["grade"]) for period, rows in eligible.items()}
  common = sorted(set(groups["train"]) & set(groups["validation"]))
  count = len if risk else _observationCount
  support = {period: _supportSummary(parts[period], eligible[period], common) for period in PERIOD_NAMES}
  commonCounts = {period: sum(count(groups[period][grade]) for grade in common) for period in PERIOD_NAMES}
  result = {"status": "ok" if common else "insufficient_common_support", "metric": metric,
            "weighting": "paired_days" if risk else "observations", "commonGrades": common,
            "within": None, "mix": None, "total": None, "trainMean": None, "validationMean": None,
            "byGrade": {}, **support}
  for grade in sorted({row["grade"] for rows in parts.values() for row in rows}):
    item = {"isCommon": grade in common}
    for period in PERIOD_NAMES:
      group = groups[period].get(grade, [])
      total = count(eligible[period])
      item.update({period + "Mean": _dailySummary(group)[metric],
                   period + "Share": count(group) / commonCounts[period] if grade in common else None,
                   period + "OverallShare": count(group) / total if total else None,
                   period + "DayCount": len(group), period + "ObservationCount": _observationCount(group)})
    result["byGrade"][grade] = item
  if common:
    terms = [result["byGrade"][grade] for grade in common]
    # 两期份额与均值对称取中点，避免把任一期人为指定为分解基准。
    result["within"] = sum((item["validationShare"] + item["trainShare"]) / 2
                           * (item["validationMean"] - item["trainMean"]) for item in terms)
    result["mix"] = sum((item["validationShare"] - item["trainShare"])
                        * (item["validationMean"] + item["trainMean"]) / 2 for item in terms)
    for period in PERIOD_NAMES:
      result[period + "Mean"] = sum(item[period + "Share"] * item[period + "Mean"] for item in terms)
    result["total"] = result["validationMean"] - result["trainMean"]
  return result


def _differenceOrNone(left, right):
  """任一分段没有有效指标时，跨期差异保持未知。"""
  return left - right if left is not None and right is not None else None


def _leaveOneMonthOut(rows):
  """逐个自然月删除原始日记录后重算总体差，不重新选择模型或环境。"""
  result = {}
  for month in sorted({row["date"][:7] for row in rows}):
    remaining = [row for row in rows if row["date"][:7] != month]
    summaries = {period: _dailySummary([row for row in remaining if row["period"] == period])
                 for period in PERIOD_NAMES}
    result[month] = {"removedDayCount": len(rows) - len(remaining), **summaries,
                     "riskShiftPct": _differenceOrNone(summaries["validation"]["meanRiskDeltaPct"],
                                                       summaries["train"]["meanRiskDeltaPct"]),
                     "sealShiftPp": _differenceOrNone(summaries["validation"]["sealDeltaPp"],
                                                      summaries["train"]["sealDeltaPp"])}
  return result


def summarizePeriodShift(dailyRows):
  """汇总一个固定模型相对基准的开发期变化，不修改输入。

  参数：dailyRows 为每日字典序列，包含 date、grade、两侧 MeanPct、SelectedCount、
  SealCount 与 sharedCount；风险允许 None，两侧选择数量必须相同且大于零。
  返回：train/validation 的总体、环境和月度统计，风险与封板增量的共同环境对称分解，
  以及逐月删除后的分段总体差。风险按配对日等权，封板正记录差按观察数加权。
  封板率仅为正记录下界；没有记录的事件保持未知，不能推导真实失败率。
  异常：非法日期、旧留出、未来、重复日、未知环境或非法数值抛出中文 ValueError。
  """
  rows = _validateRows(dailyRows)
  parts = {period: [row for row in rows if row["period"] == period] for period in PERIOD_NAMES}
  return {**{period: _periodSummary(part) for period, part in parts.items()},
          "riskDecomposition": _decompose(parts, "meanRiskDeltaPct"),
          "sealDecomposition": _decompose(parts, "sealDeltaPp"),
          "leaveOneMonthOut": _leaveOneMonthOut(rows)}
