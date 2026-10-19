"""固定开发组合的配对风险归因；仅作描述性诊断，不选择新权重。"""

# 项目研究模块采用小驼峰命名。
# ruff: noqa: N999

import math
from collections import defaultdict
from datetime import date
from statistics import mean

from quant_forge.research.observationEvaluation import PERIODS, TAIL_FRACTION
from quant_forge.research.observationStudy import FEATURE_NAMES

TOP_CONTRIBUTORS = 5
PERCENT_SCALE = 100.0
EVENT_GROUPS = ("recordedSeal", "recordedBreak", "unrecordedUnknown")
SIDES = ("candidate", "baseline")


def developmentPeriod(day):
  """返回固定开发分段；无效日期、旧留出或未来日期抛出中文 ValueError。"""
  try:
    valid = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
  except ValueError:
    valid = False
  for period in ("train", "validation"):
    if valid and PERIODS[period][0] <= day <= PERIODS[period][1]:
      return period
  raise ValueError(f"风险诊断只允许固定开发区间：{day}")


def _eventGroup(row):
  """只把有记录的正事件分类，池外标签保持未知。"""
  if row["outcome"]["sealEvent"] is True:
    return "recordedSeal"
  if row["outcome"]["touchEvent"] is True:
    return "recordedBreak"
  return "unrecordedUnknown"


def _validLoss(row):
  """保留未知下行；拒绝非有限、布尔及负数，避免污染贡献。"""
  value = row["outcome"]["downsidePct"]
  if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                            or not math.isfinite(value) or value < 0):
    raise ValueError("配对归因包含非法下行结果")
  return value


def attributeDay(day, grade, candidateRows, baselineRows):
  """返回每日摘要、各证券有符号贡献和盘前特征对照，不修改输入。

  两个名单必须为相同数量的同日选择；共同代码须具有相同结果。
  下行贡献分别使用各自有效结果数。任一侧无有效结果则贡献未知，不能填零。
  非开发日期、重复代码、不匹配数量或不一致结果抛出中文 ValueError。
  """
  period = developmentPeriod(day)
  sides = {"candidate": candidateRows, "baseline": baselineRows}
  maps = {side: {row["code"]: row for row in rows} for side, rows in sides.items()}
  if len(candidateRows) != len(baselineRows) or any(len(maps[s]) != len(sides[s]) for s in SIDES):
    raise ValueError("配对名单数量不同或存在重复代码")
  for code in maps["candidate"].keys() & maps["baseline"].keys():
    if maps["candidate"][code]["outcome"] != maps["baseline"][code]["outcome"]:
      raise ValueError("配对共同股票的结果不一致")
  daily = {"date": day, "grade": grade, "period": period,
           "sharedCount": len(maps["candidate"].keys() & maps["baseline"].keys())}
  features = {name: {} for name in FEATURE_NAMES}
  for side, rows in sides.items():
    losses = [_validLoss(row) for row in rows]
    valid = [loss for loss in losses if loss is not None]
    daily.update({side + "SelectedCount": len(rows), side + "ValidCount": len(valid),
                  side + "MissingCount": len(rows) - len(valid), side + "MeanPct": mean(valid) if valid else None,
                  side + "SealCount": sum(row["outcome"]["sealEvent"] is True for row in rows)})
    for index, name in enumerate(FEATURE_NAMES):
      features[name].update({side + "Raw": mean(row["features"][name] for row in rows) if rows else None,
                            side + "Rank": mean(row["ranks"][index] for row in rows) if rows else None})
  paired = all(daily[s + "ValidCount"] for s in SIDES)
  daily["deltaPct"] = daily["candidateMeanPct"] - daily["baselineMeanPct"] if paired else None
  stocks = []
  if paired:
    for code in sorted(maps["candidate"].keys() | maps["baseline"].keys()):
      row = maps["candidate"].get(code, maps["baseline"].get(code))
      stock = {"code": code, "eventGroup": _eventGroup(row), "contributionPct": 0.0}
      for side, sign in (("candidate", 1), ("baseline", -1)):
        present = code in maps[side]
        loss = _validLoss(maps[side][code]) if present else None
        stock[side + "Count"] = int(present)
        stock[side + "MissingCount"] = int(present and loss is None)
        term = loss / daily[side + "ValidCount"] if loss is not None else 0.0
        stock[side + "ContributionPct"] = term
        stock["contributionPct"] += sign * term
      stocks.append(stock)
  return {"daily": daily, "stocks": stocks, "features": features}


def _meanOrNone(values):
  """空样本保持未知。"""
  return mean(values) if values else None


def _dailySummary(rows):
  """按日期等权汇总；两边尾部使用各自最差日期，不能解释为可加归因。"""
  usable = [r for r in rows if r["deltaPct"] is not None]
  result = {"dayCount": len(rows), "pairedDays": len(usable), "unpairedDays": len(rows) - len(usable),
            "meanDeltaPct": _meanOrNone([r["deltaPct"] for r in usable]),
            "higherRiskDayFraction": sum(r["deltaPct"] > 0 for r in usable) / len(usable) if usable else None}
  for side in SIDES:
    values = [r[side + "MeanPct"] for r in usable]
    tail = sorted(usable, key=lambda r: (-r[side + "MeanPct"], r["date"]))
    tail = tail[:math.ceil(len(tail) * TAIL_FRACTION)]
    result.update({side + "MeanPct": _meanOrNone(values),
                   side + "TailPct": _meanOrNone([r[side + "MeanPct"] for r in tail]),
                   side + "TailDates": [r["date"] for r in tail],
                   side + "SelectedCount": sum(r[side + "SelectedCount"] for r in rows),
                   side + "ValidCount": sum(r[side + "ValidCount"] for r in rows),
                   side + "SealCount": sum(r[side + "SealCount"] for r in rows),
                   side + "MissingCount": sum(r[side + "MissingCount"] for r in rows)})
  return result


def _groupDays(rows, key):
  """同一组合内按环境或月份分组，不混合组合样本。"""
  groups = defaultdict(list)
  for row in rows:
    groups[key(row)].append(row)
  return {name: _dailySummary(group) for name, group in sorted(groups.items())}


def _aggregateStocks(records, key, pairedCount):
  """证券或事件组贡献按同一组日期分母累加，并保留两侧及缺失数量。"""
  groups = {}
  for record in records:
    for row in record["stocks"]:
      name = key(row)
      aggregate = groups.setdefault(name, {"key": name, "contributionSumPct": 0.0,
        "candidateCount": 0, "baselineCount": 0, "candidateMissingCount": 0, "baselineMissingCount": 0,
        "candidateContributionSumPct": 0.0, "baselineContributionSumPct": 0.0})
      aggregate["contributionSumPct"] += row["contributionPct"]
      for side in SIDES:
        for field in ("Count", "MissingCount"):
          aggregate[side + field] += row[side + field]
        aggregate[side + "ContributionSumPct"] += row[side + "ContributionPct"]
  for row in groups.values():
    row["meanContributionPct"] = row["contributionSumPct"] / pairedCount if pairedCount else None
  return groups


def summarizeAttribution(records):
  """汇总一个组合一个分段的日期、环境、证券、事件及盘前特征差异。

  集中度以正贡献总额为分母；事后剔除最多五个正差日期只作敏感性说明。
  不将组合间重复的证券日合并成独立观测，不返回可用于上线的权重。
  """
  daily = [r["daily"] for r in records]
  if len({r["date"] for r in daily}) != len(daily):
    raise ValueError("归因汇总日期重复，不能混合不同组合")
  summary = _dailySummary(daily)
  summary["byGrade"] = _groupDays(daily, lambda r: r["grade"])
  summary["byMonth"] = _groupDays(daily, lambda r: r["date"][:7])
  usable = [r for r in daily if r["deltaPct"] is not None]
  positive = sorted((r for r in usable if r["deltaPct"] > 0), key=lambda r: (-r["deltaPct"], r["date"]))
  top = positive[:TOP_CONTRIBUTORS]
  totalPositive = sum(r["deltaPct"] for r in positive)
  summary["topDays"] = top
  summary["topDaysPositiveSharePct"] = (sum(r["deltaPct"] for r in top) / totalPositive * PERCENT_SCALE
                                        if totalPositive else None)
  removed = {r["date"] for r in top}
  summary["withoutTopPositiveDays"] = _dailySummary([r for r in daily if r["date"] not in removed])
  stocks = _aggregateStocks(records, lambda r: r["code"], len(usable))
  summary["stocks"] = sorted(stocks.values(), key=lambda r: (-r["contributionSumPct"], r["key"]))
  positiveStocks = [r for r in summary["stocks"] if r["contributionSumPct"] > 0]
  totalStockPositive = sum(r["contributionSumPct"] for r in positiveStocks)
  summary["topStocksPositiveSharePct"] = (
    sum(r["contributionSumPct"] for r in positiveStocks[:TOP_CONTRIBUTORS]) / totalStockPositive * PERCENT_SCALE
    if totalStockPositive else None)
  summary["eventGroups"] = _aggregateStocks(records, lambda r: r["eventGroup"], len(usable))
  summary["features"] = {name: {
    side + kind: _meanOrNone([r["features"][name][side + kind] for r in records
                             if r["features"][name][side + kind] is not None])
    for side in SIDES for kind in ("Raw", "Rank")} for name in FEATURE_NAMES}
  return summary
