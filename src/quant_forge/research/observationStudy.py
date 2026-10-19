"""独立盘前观察特征与评分；历史交易资格未知，不供正式交易推荐使用。"""

# 项目研究模块采用小驼峰命名。
# ruff: noqa: N999

import math
from itertools import pairwise
from statistics import median

FEATURE_NAMES = ("downside", "momentum", "position", "liquidity")
HISTORY_ROWS = 21
RETURN_DAYS = 20
TREND_DAYS = 5
MIN_PRICE = 3.0
MIN_TURNOVER = 100_000_000.0
DEFAULT_TOP = 10
PERCENT_SCALE = 100.0
GRID_UNITS = 4
MIN_RISK_UNITS = 2
WEIGHT_TOLERANCE = 1e-9
PRICE_FIELDS = ("open_price", "high_price", "low_price", "close_price")


def validBar(row):
  """校验未复权日线有效价格与正成交量额，空行返回 False。"""
  if not isinstance(row, dict):
    return False
  for field in (*PRICE_FIELDS, "volume", "turnover"):
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
      return False
    try:
      if not math.isfinite(value) or value <= 0:
        return False
    except OverflowError:
      return False
  return row["low_price"] <= min(row["open_price"], row["close_price"]) <= max(
    row["open_price"], row["close_price"]
  ) <= row["high_price"]


def buildFeatures(history, expectedDates, eventDates):
  """返回四特征或排除原因；只接受按日历排列的 21 根 D 及以前日线。

  参数：history 为日线列表，expectedDates 为日期字符串列表，eventDates 为该证券除权生效日集合。
  返回：(特征字典, None) 或 (None, 原因)，不填补缺行、不读取任何 T 日信息。
  """
  if len(expectedDates) != HISTORY_ROWS or len(history) != HISTORY_ROWS:
    return None, "MISSING_HISTORY"
  if any(row is None or row.get("date") != day for row, day in zip(history, expectedDates)):
    return None, "MISSING_HISTORY"
  if expectedDates != sorted(set(expectedDates)):
    raise ValueError("特征参考日期必须严格递增且没有重复")
  if not all(validBar(row) for row in history):
    return None, "INVALID_HISTORY"
  # 首日仅作收益基价；首日已生效的事件不跨越任何日收益，未来事件不参与过滤。
  if any(day in eventDates for day in expectedDates[1:]):
    return None, "EX_DATE_WINDOW"
  if history[-1]["close_price"] < MIN_PRICE:
    return None, "LOW_PRICE"
  liquidity = median(row["turnover"] for row in history[-RETURN_DAYS:])
  if liquidity < MIN_TURNOVER:
    return None, "LOW_LIQUIDITY"
  returns = [(b["close_price"] / a["close_price"] - 1) * PERCENT_SCALE for a, b in pairwise(history)]
  positions = [(row["close_price"] - row["low_price"]) / (row["high_price"] - row["low_price"])
               if row["high_price"] > row["low_price"] else 0.5 for row in history[-TREND_DAYS:]]
  features = {
    "downside": math.hypot(*(min(value, 0.0) for value in returns)) / math.sqrt(RETURN_DAYS),
    "momentum": (history[-1]["close_price"] / history[-TREND_DAYS - 1]["close_price"] - 1) * PERCENT_SCALE,
    "position": sum(positions) / TREND_DAYS,
    "liquidity": liquidity,
  }
  if not all(math.isfinite(value) for value in features.values()):
    return None, "NON_FINITE_FEATURE"
  return features, None


def buildOutcome(row, upMember, breakMember):
  """返回 T 日日内观察与事件正标签；缺行情和非池成员保持未知，双池交集抛出 ValueError。"""
  if type(upMember) is not bool or type(breakMember) is not bool:
    raise ValueError("池成员标志必须为布尔值")
  if upMember and breakMember:
    raise ValueError("结果存在同日双池交集")
  result = {"downsidePct": None, "upsidePct": None, "closePct": None,
            "touchEvent": True if upMember or breakMember else None,
            "sealEvent": True if upMember else None}
  if validBar(row):
    opening = row["open_price"]
    values = ((opening - row["low_price"]) / opening * PERCENT_SCALE,
              (row["high_price"] / opening - 1) * PERCENT_SCALE,
              (row["close_price"] / opening - 1) * PERCENT_SCALE)
    if all(math.isfinite(value) for value in values):
      result.update(zip(("downsidePct", "upsidePct", "closePct"), values))
  return result


def validateWeights(weights):
  """校验非负、有限、合计为一的四权重；异常统一抛出中文 ValueError。"""
  if not isinstance(weights, (list, tuple)) or len(weights) != len(FEATURE_NAMES):
    raise ValueError("权重必须包含四个数字")
  try:
    valid = all(not isinstance(w, bool) and isinstance(w, (int, float)) and math.isfinite(w) and w >= 0
                for w in weights)
    valid = valid and abs(sum(weights) - 1) <= WEIGHT_TOLERANCE
  except OverflowError:
    valid = False
  if not valid:
    raise ValueError("权重须为非负有限数字，且合计为一")


def weightGrid():
  """返回事先冻结的十组权重，下行稳定性至少一半，不根据结果扩大搜索。"""
  return [(risk / GRID_UNITS, trend / GRID_UNITS, position / GRID_UNITS,
           (GRID_UNITS - risk - trend - position) / GRID_UNITS)
          for risk in range(MIN_RISK_UNITS, GRID_UNITS + 1)
          for trend in range(GRID_UNITS - risk + 1)
          for position in range(GRID_UNITS - risk - trend + 1)]


def rankCandidates(rows):
  """将同日四特征变为平均秩百分位，稳定性反向；不读取或修改候选结果字段。"""
  if len({row["code"] for row in rows}) != len(rows):
    raise ValueError("同日候选代码重复")
  ranks = [[0.5] * len(FEATURE_NAMES) for _ in rows]
  for featureIndex, field in enumerate(FEATURE_NAMES):
    if any(not math.isfinite(row["features"][field]) for row in rows):
      raise ValueError("候选特征必须为有限数字")
    ordered = sorted(range(len(rows)), key=lambda i: rows[i]["features"][field])
    start = 0
    while start < len(rows):
      end = start + 1
      while end < len(rows) and rows[ordered[start]]["features"][field] == rows[ordered[end]]["features"][field]:
        end += 1
      rank = (start + end - 1) / (2 * (len(rows) - 1)) if len(rows) > 1 else 0.5
      for index in ordered[start:end]:
        ranks[index][featureIndex] = 1 - rank if field == "downside" else rank
      start = end
  return [{**row, "ranks": tuple(ranks[i])} for i, row in enumerate(rows)]


def selectCandidates(rows, weights, *, top=DEFAULT_TOP):
  """按预先算好的同日百分位选前 N；不足 N 时为空，分数相同按代码；非法参数抛出 ValueError。"""
  validateWeights(weights)
  if type(top) is not int or top <= 0:
    raise ValueError("观察数量必须为正整数")
  if len(rows) < top:
    return []
  return sorted(rows, key=lambda row: (-sum(w * v for w, v in zip(weights, row["ranks"])), row["code"]))[:top]
