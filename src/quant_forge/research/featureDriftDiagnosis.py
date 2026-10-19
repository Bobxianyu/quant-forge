"""仅描述固定开发区间的盘前特征分布，不选择权重或补造负事件标签。"""

# 项目研究模块采用小驼峰命名。
# ruff: noqa: N999

import math
from bisect import bisect_left
from collections import defaultdict
from statistics import mean

from quant_forge.research.weightRiskDiagnosis import developmentPeriod

FEATURE_FIELDS = ("intradayMean20", "intradayTail20", "momentum", "position", "liquidity")
DEVELOPMENT_PERIODS = ("train", "validation")
QUARTILE_FRACTIONS = (0.25, 0.5, 0.75)
PERCENT_SCALE = 100.0
ROW_FIELDS = ("date", "code", "features", "downsidePct", "sealEvent", "touchEvent")
EVENT_FIELDS = ("sealEvent", "touchEvent")


def _validateNumber(value, label, *, nonnegative=False):
  """校验有限数并拒绝布尔值，数值不合法时给出中文错误。"""
  validType = not isinstance(value, bool) and isinstance(value, (int, float))
  try:
    finite = validType and math.isfinite(value)
  except OverflowError:
    finite = False
  if not finite or (nonnegative and value < 0):
    raise ValueError(f"特征分布诊断包含非法{label}")


def _validateRows(rows):
  """检查完整证券日记录并返回索引和开发分段，不改写输入。"""
  if not isinstance(rows, list):
    raise ValueError("特征分布诊断记录必须为列表")  # noqa: TRY004 -- 研究校验契约统一使用 ValueError。
  indexedRows, periods = {}, {}
  for row in rows:
    if not isinstance(row, dict) or not set(ROW_FIELDS).issubset(row):
      raise ValueError("特征分布诊断记录缺少必要字段或不是字典")
    period = developmentPeriod(row["date"])
    if not isinstance(row["code"], str) or not row["code"].strip():
      raise ValueError("特征分布诊断证券代码必须为非空字符串")
    key = (row["date"], row["code"])
    if key in indexedRows:
      raise ValueError("特征分布诊断存在重复证券日记录")
    features = row["features"]
    if not isinstance(features, dict) or set(features) != set(FEATURE_FIELDS):
      raise ValueError("特征分布诊断必须包含且仅包含固定的五项盘前特征")
    for field in FEATURE_FIELDS:
      _validateNumber(features[field], f"特征数值：{field}")
    if row["downsidePct"] is not None:
      _validateNumber(row["downsidePct"], "下行结果", nonnegative=True)
    for field in EVENT_FIELDS:
      if row[field] is not True and row[field] is not None:
        raise ValueError(f"特征分布诊断事件标签只允许 True 或 None：{field}")
    if row["sealEvent"] is True and row["touchEvent"] is not True:
      raise ValueError("特征分布诊断已记录封板必须同时记录触板")
    indexedRows[key], periods[key] = row, period
  return indexedRows, periods


def _validateContexts(contexts, indexedRows):
  """上下文只能引用已校验的证券日集合，避免静默丢失未知样本。"""
  if not isinstance(contexts, dict):
    raise ValueError("特征分布诊断上下文必须为字典")  # noqa: TRY004 -- 研究校验契约统一使用 ValueError。
  for name, keys in contexts.items():
    if not isinstance(name, str) or not name.strip() or not isinstance(keys, (set, frozenset)):
      raise ValueError("特征分布诊断上下文必须由非空名称映射到证券日集合")
    for key in keys:
      if not isinstance(key, tuple) or len(key) != 2 or key not in indexedRows:
        raise ValueError(f"特征分布诊断上下文引用了未知或非法证券日：{name}")


def _quartiles(values):
  """以排序位置线性插值求四分位边界，允许相等边界及后续空箱。"""
  ordered = sorted(values)
  result = []
  for fraction in QUARTILE_FRACTIONS:
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    weight = position - lower
    # 使用加权和，避免两个有限但符号相反的极端值相减后溢出。
    result.append(ordered[lower] * (1 - weight) + ordered[upper] * weight)
  return result


def _meanOrNone(values):
  """空样本保留未知均值。"""
  return mean(values) if values else None


def _outcomeSummary(rows):
  """统计已记录正事件，按每日有效结果均值再次等权汇总下行。"""
  dailyLosses = defaultdict(list)
  validCount = 0
  for row in rows:
    loss = row["downsidePct"]
    if loss is not None:
      dailyLosses[row["date"]].append(loss)
      validCount += 1
  touchCount = sum(row["touchEvent"] is True for row in rows)
  return {"selectedCount": len(rows), "recordedSealCount": sum(row["sealEvent"] is True for row in rows),
          "recordedTouchCount": touchCount, "unknownEventCount": len(rows) - touchCount,
          "validOutcomeCount": validCount,
          "meanDownsidePct": _meanOrNone([mean(losses) for losses in dailyLosses.values()])}


def _binSummary(rows):
  """箱内占比以全部入箱记录为分母，仅表示已记录封板占比。"""
  result = _outcomeSummary(rows)
  count = result.pop("selectedCount")
  sealCount = result.pop("recordedSealCount")
  touchCount = result.pop("recordedTouchCount")
  return {"count": count, "sealCount": sealCount, "touchCount": touchCount, **result,
          "recordedSealSharePct": sealCount / count * PERCENT_SCALE if count else None}


def _describePeriod(rows, thresholds):
  """使用固定训练阈值描述一个上下文分段，特征均值按记录等权。"""
  result = _outcomeSummary(rows)
  features = {}
  for field in FEATURE_FIELDS:
    bins = [[] for _ in range(len(QUARTILE_FRACTIONS) + 1)]
    for row in rows:
      # 左侧插入规则使等于任一边界的值落入左箱，包括重复边界。
      bins[bisect_left(thresholds[field], row["features"][field])].append(row)
    features[field] = {
      "mean": _meanOrNone([row["features"][field] for row in rows]),
      "sealedMean": _meanOrNone([row["features"][field] for row in rows if row["sealEvent"] is True]),
      "bins": [_binSummary(group) for group in bins],
    }
  return {**result, "features": features}


def describeFeatureCohorts(rows, contexts):
  """描述指定证券日集合在训练及验证期的盘前特征分布，不修改输入。

  参数：rows 为固定五项有限特征及下行、正事件标签的记录列表；contexts 为名称到证券日集合的映射。
  返回：thresholds 存放各特征三个训练四分位边界，cohorts 存放上下文按开发分段汇总的统计。
  异常：非法记录、非开发日期、未知上下文键或没有训练记录时抛出中文 ValueError。
  边界只用全部训练记录拟合，验证记录仅按固定边界分箱；空样本和缺失结果均不填零。
  """
  indexedRows, periods = _validateRows(rows)
  _validateContexts(contexts, indexedRows)
  trainRows = [row for key, row in indexedRows.items() if periods[key] == "train"]
  if not trainRows:
    raise ValueError("特征分布诊断缺少训练记录，不能拟合四分位边界")
  thresholds = {field: _quartiles([row["features"][field] for row in trainRows]) for field in FEATURE_FIELDS}
  cohorts = {}
  for name, keys in contexts.items():
    partitions = {period: [] for period in DEVELOPMENT_PERIODS}
    for key in sorted(keys):
      partitions[periods[key]].append(indexedRows[key])
    cohorts[name] = {period: _describePeriod(partitions[period], thresholds) for period in DEVELOPMENT_PERIODS}
  return {"thresholds": thresholds, "cohorts": cohorts}
