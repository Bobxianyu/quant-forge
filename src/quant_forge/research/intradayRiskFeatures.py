"""仅使用观察日前日线计算历史盘中下行均值与固定尾部风险。"""

# 项目研究模块采用小驼峰命名。
# ruff: noqa: N999

import math
from datetime import date

RISK_VARIANTS = ("intradayMean20", "intradayTail20")
HISTORY_DAYS = 20
TAIL_DAYS = 2
PERCENT_SCALE = 100.0
PRICE_FIELDS = ("open_price", "low_price")


def validateDate(value, label):
  """校验标准年月日字符串，非法日期统一抛出含字段位置的中文 ValueError。"""
  if not isinstance(value, str):
    raise ValueError(f"{label}日期必须为有效的 YYYY-MM-DD 字符串")  # noqa: TRY004
  try:
    parsed = date.fromisoformat(value)
  except ValueError as error:
    raise ValueError(f"{label}日期无效：{value}") from error
  if parsed.isoformat() != value:
    raise ValueError(f"{label}日期必须使用 YYYY-MM-DD 格式：{value}")


def validatePrice(value, field, day):
  """校验正数且有限的价格；拒绝布尔、字符串和不能安全转换的超大整数。"""
  valid = not isinstance(value, bool) and isinstance(value, (int, float))
  try:
    valid = valid and math.isfinite(value) and value > 0
  except OverflowError:
    valid = False
  if not valid:
    raise ValueError(f"历史价格无效：{day} 的 {field} 必须为正数且有限")


def computeIntradayRisk(history, expectedDates, targetDate):
  """返回 20 日盘中下行均值和最大两日均值，不读取标签或修改输入。

  参数：history 为按日期排列的 20 个日线字典；expectedDates 为相同顺序的 20 个标准日期；
  targetDate 为观察日期，全部历史日期必须早于该日期。
  返回：intradayMean20 和 intradayTail20 两个百分数数值。
  异常：行数、日期对齐、时间边界或价格不合法时抛出中文 ValueError，不补零或删行。
  """
  validateDate(targetDate, "观察日")
  if not isinstance(history, (list, tuple)) or len(history) != HISTORY_DAYS:
    raise ValueError(f"历史日线必须恰有 {HISTORY_DAYS} 行")
  if not isinstance(expectedDates, (list, tuple)) or len(expectedDates) != HISTORY_DAYS:
    raise ValueError(f"参考日期必须恰有 {HISTORY_DAYS} 个")
  previousDate = None
  values = []
  for row, day in zip(history, expectedDates):
    validateDate(day, "参考")
    if previousDate is not None and day <= previousDate:
      raise ValueError("参考日期必须严格递增且没有重复")
    if day >= targetDate:
      raise ValueError(f"历史日期 {day} 必须严格早于观察日 {targetDate}")
    if not isinstance(row, dict) or row.get("date") != day:
      raise ValueError(f"历史日线日期未与参考日期 {day} 对齐")
    for field in PRICE_FIELDS:
      validatePrice(row.get(field), field, day)
    opening, low = row["open_price"], row["low_price"]
    if low > opening:
      raise ValueError(f"历史价格无效：{day} 的最低价不能高于开盘价")
    # 校验完整历史后按当日开盘为分母，避免引入收盘收益或观察日结果。
    values.append((opening - low) / opening * PERCENT_SCALE)
    previousDate = day
  return {
    "intradayMean20": math.fsum(values) / HISTORY_DAYS,
    "intradayTail20": math.fsum(sorted(values, reverse=True)[:TAIL_DAYS]) / TAIL_DAYS,
  }


def validateCalendar(calendar):
  """校验参考交易日历严格递增且为标准日期，返回日期位置映射。"""
  if not isinstance(calendar, (list, tuple)):
    raise ValueError("参考日历必须为日期列表或元组")  # noqa: TRY004
  previousDate = None
  positions = {}
  for index, day in enumerate(calendar):
    validateDate(day, "参考日历")
    if previousDate is not None and day <= previousDate:
      raise ValueError("参考日历日期必须严格递增且没有重复")
    positions[day] = index
    previousDate = day
  return positions


def indexHistoryBars(code, bars):
  """按日期索引单证券日线，拒绝重复日期；未使用日期的价格不参与校验和计算。"""
  if not isinstance(bars, (list, tuple)):
    raise ValueError(f"证券 {code} 的历史日线必须为列表或元组")  # noqa: TRY004
  indexed = {}
  for row in bars:
    if not isinstance(row, dict):
      raise ValueError(f"证券 {code} 的历史日线必须为字典")  # noqa: TRY004
    day = row.get("date")
    validateDate(day, f"证券 {code} 的历史")
    if day in indexed:
      raise ValueError(f"证券 {code} 的历史日线日期重复：{day}")
    indexed[day] = row
  return indexed


def buildRiskFeatureRows(code, bars, calendar, targetDates):
  """按交易日历为单证券的每个观察日期构建完整盘前风险附表，不修改输入。

  参数：code 为证券代码；bars 为至少含日期、开盘价和最低价的日线；calendar 为严格递增的
  参考交易日期；targetDates 为不重复的观察日期，允许为空并保留调用者给定顺序。
  返回：含 date、code、historyStart、historyEnd 及两个风险特征的字典列表。
  异常：日期重复、观察日缺失或历史不足时抛出中文 ValueError；窗口缺口附证券和观察日期。
  """
  if not isinstance(code, str) or not code.strip():
    raise ValueError("证券代码必须为非空字符串")
  if not isinstance(targetDates, (list, tuple)):
    raise ValueError(f"证券 {code} 的观察日期必须为列表或元组")  # noqa: TRY004
  positions = validateCalendar(calendar)
  indexed = indexHistoryBars(code, bars)
  seenTargets = set()
  result = []
  for target in targetDates:
    validateDate(target, f"证券 {code} 的观察")
    if target in seenTargets:
      raise ValueError(f"证券 {code} 的观察日期重复：{target}")
    seenTargets.add(target)
    if target not in positions:
      raise ValueError(f"证券 {code} 的观察日 {target} 不在参考日历中")
    index = positions[target]
    if index < HISTORY_DAYS:
      raise ValueError(f"证券 {code} 的观察日 {target} 缺少此前 {HISTORY_DAYS} 个参考交易日")
    # 只取 T 之前的二十个参考交易日，不按自然日倒推，也不读取 T 日及以后价格。
    expectedDates = calendar[index - HISTORY_DAYS:index]
    missing = [day for day in expectedDates if day not in indexed]
    if missing:
      raise ValueError(f"证券 {code} 的观察日 {target} 缺少历史日线：{', '.join(missing)}")
    try:
      features = computeIntradayRisk([indexed[day] for day in expectedDates], expectedDates, target)
    except ValueError as error:
      raise ValueError(f"证券 {code} 的观察日 {target} 风险计算失败：{error}") from error
    result.append({"date": target, "code": code, "historyStart": expectedDates[0],
                   "historyEnd": expectedDates[-1], **features})
  return result
