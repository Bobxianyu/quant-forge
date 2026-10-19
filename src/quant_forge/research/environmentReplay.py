"""以冻结收盘序列回放已有环境规则，不拟合权重或生成股票、账户回测。

首板序列的完整日期集合充当参考日历，调用方须事先核验它的完整性。
评估日 T 只观察首板下一期表现；特征严格来自前两参考日 P、D。
minIndexClose 是研究质量过滤值，不能证明供应商精度，也不能代替价格口径核验。
"""

# 项目工具模块采用小驼峰文件名。
# ruff: noqa: N999

import math
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, localcontext
from statistics import mean
from typing import Any

from quant_forge.config import EnvironmentRuleConfig, load_default_config
from quant_forge.domain.models import MarketEnvironment, MarketPremiumSnapshot, PermissionGrade
from quant_forge.rules.environment import assess_environment

FIRST_INDEX = "883994.TI"
INDEX_FIELDS = {
  FIRST_INDEX: "first_board_premium_pct",
  "883997.TI": "second_board_premium_pct",
  "883410.TI": "multi_board_premium_pct",
  "883900.TI": "limit_up_premium_pct",
}
DEFAULT_MIN_INDEX_CLOSE = 1.0
PERCENT_SCALE = 100.0
RETURN_DECIMAL_PRECISION = 34
LOWER_QUANTILE = 0.1
FEATURE_HISTORY_LENGTH = 2
SHANGHAI = timezone(timedelta(hours=8))
RESEARCH_OBSERVATION_TIME = time(15)
RESEARCH_SOURCE = "冻结收盘研究回放；时间字段为合成观察时点，不代表真实采集"
STATISTIC_FIELDS = (
  "meanReturnPct", "medianReturnPct", "positiveReturnRatePct", "lowerDecileReturnPct", "worstReturnPct",
)


def replayEnvironment(
  indexCloses: Mapping[str, Mapping[date, float]],
  *,
  evaluationStart: date,
  evaluationEnd: date,
  minIndexClose: float = DEFAULT_MIN_INDEX_CLOSE,
) -> dict[str, Any]:
  """返回固定区间的逐日环境、排除记录及分组统计，不修改输入。

  参数：indexCloses 使用四个指数代码与 datetime.date 键；起止日期均包含在评估区间。
  minIndexClose 必须为有限非负数；零可关闭低精度过滤，但不能允许零价或负价。
  返回：可严格 JSON 序列化的研究字典，收益、均值和正收益率均以百分数表示。
  异常：输入结构、日期或研究阈值无效时抛出包含中文说明的 ValueError。
  说明：仅调用 assess_environment，基础权限不包含外围、新闻风险及仓位规则。
  """
  _validateInputs(indexCloses, evaluationStart, evaluationEnd, minIndexClose)
  rules = load_default_config().environment
  calendar = sorted(indexCloses[FIRST_INDEX])
  observations = []
  excluded = []
  referenceDateCount = 0
  for position, targetDate in enumerate(calendar):
    if not evaluationStart <= targetDate <= evaluationEnd:
      continue
    referenceDateCount += 1
    if position < FEATURE_HISTORY_LENGTH:
      excluded.append({
        "targetDate": targetDate.isoformat(),
        "reasons": [{"code": "INSUFFICIENT_HISTORY", "message": "缺少前两个参考交易日，不能构造历史特征"}],
      })
      continue
    previousDate, signalDate = calendar[position - FEATURE_HISTORY_LENGTH:position]
    observation, reasons = _evaluateTarget(indexCloses, previousDate, signalDate, targetDate, minIndexClose, rules)
    if reasons:
      excluded.append({"targetDate": targetDate.isoformat(), "reasons": reasons})
    else:
      observations.append(observation)

  groups = {}
  for field, choices in (("permissionGrade", PermissionGrade), ("environment", MarketEnvironment)):
    groups[field] = {
      choice.value: _summarize([row["targetReturnPct"] for row in observations if row[field] == choice.value])
      for choice in choices
    }
  return {
    "analysisType": "ENVIRONMENT_REPLAY_ONLY",
    "evaluationStart": evaluationStart.isoformat(),
    "evaluationEnd": evaluationEnd.isoformat(),
    "minIndexClose": minIndexClose,
    "strongPremiumThresholdPct": rules.strong_premium_threshold_pct,
    "lowerQuantile": LOWER_QUANTILE,
    "referenceDateCount": referenceDateCount,
    "featureDefinition": "四指数 close(D) / close(P) - 1；P、D 为 T 前两个参考交易日",
    "targetDefinition": "首板 close(T) / close(D) - 1；仅指数观察结果，不是交易收益",
    "limitations": [
      "合成观察时点仅满足环境对象字段要求，不代表历史数据当时已经采集，不调用正式盘前管道",
      "最低指数收盘门槛只是研究过滤，不能证明供应商历史精度或消除幸存者偏差",
      "基础环境权限不含新闻和外围风险闸门，不是股票推荐或账户回测",
      "分组样本较少时统计不稳定，不能单凭分组均值认定规则有效",
    ],
    "observations": observations,
    "excluded": excluded,
    "summary": _summarize([row["targetReturnPct"] for row in observations]),
    "groups": groups,
  }


def _validateInputs(indexCloses, evaluationStart, evaluationEnd, minIndexClose) -> None:
  """校验调用契约；逐日价格缺陷由排除记录解释，不能静默清洗。"""
  if type(evaluationStart) is not date or type(evaluationEnd) is not date or evaluationStart > evaluationEnd:
    raise ValueError("评估起止日期必须为 date，且开始日期不能晚于结束日期")
  if not _isFiniteNumber(minIndexClose) or minIndexClose < 0:
    raise ValueError("minIndexClose 必须为有限非负数")
  if not isinstance(indexCloses, Mapping):
    raise ValueError("指数收盘输入必须为代码到日期序列的映射")  # noqa: TRY004 -- 对外契约统一使用 ValueError。
  for code in INDEX_FIELDS:
    if code not in indexCloses:
      raise ValueError(f"缺少指数收盘序列：{code}")
    if not isinstance(indexCloses[code], Mapping):
      raise ValueError(f"指数 {code} 的收盘序列必须为日期映射")  # noqa: TRY004 -- 对外契约统一使用 ValueError。
    if any(type(day) is not date for day in indexCloses[code]):
      raise ValueError(f"指数 {code} 的日期键必须为 date，不能混用文本或 datetime")


def _evaluateTarget(indexCloses, previousDate, signalDate, targetDate, minimum, rules):
  """先逐项校验 P/D 和首板 T，再计算特征，绝不跨越缺失日取前值。"""
  reasons = []
  for code in INDEX_FIELDS:
    for day, role in ((previousDate, "featurePrevious"), (signalDate, "featureSignal")):
      issue = _closeIssue(indexCloses[code], code, day, role, minimum)
      if issue:
        reasons.append(issue)
  issue = _closeIssue(indexCloses[FIRST_INDEX], FIRST_INDEX, targetDate, "target", minimum)
  if issue:
    reasons.append(issue)
  if reasons:
    return None, reasons

  features = {}
  for code in INDEX_FIELDS:
    features[code] = _returnPct(indexCloses[code][signalDate], indexCloses[code][previousDate])
    if not math.isfinite(features[code]):
      reasons.append(_returnIssue(code, signalDate, "featureSignal"))
  targetReturn = _returnPct(indexCloses[FIRST_INDEX][targetDate], indexCloses[FIRST_INDEX][signalDate])
  if not math.isfinite(targetReturn):
    reasons.append(_returnIssue(FIRST_INDEX, targetDate, "target"))
  if reasons:
    return None, reasons

  assessment = _assessFeatures(features, signalDate, rules)
  return {
    "targetDate": targetDate.isoformat(),
    "signalDate": signalDate.isoformat(),
    "previousDate": previousDate.isoformat(),
    "featureReturnsPct": features,
    "environment": assessment.environment.value,
    "marketStyle": assessment.market_style.value,
    "permissionGrade": assessment.permission_grade.value,
    "recoveryStrength": assessment.recovery_strength.value if assessment.recovery_strength else None,
    "targetReturnPct": targetReturn,
  }, []


def _returnPct(currentClose, previousClose) -> float:
  """按输入价格的十进制表示计算涨幅，避免恰好处于权限阈值时被浮点误差升级。"""
  with localcontext() as context:
    context.prec = RETURN_DECIMAL_PRECISION
    # 不做固定小数位四舍五入，以保留阈值两侧实际存在的微小差异。
    return float((Decimal(str(currentClose)) / Decimal(str(previousClose)) - 1) * Decimal(str(PERCENT_SCALE)))


def _closeIssue(series, code, day, role, minimum):
  """返回明确的数据排除原因，不把异常值写进严格 JSON 结果。"""
  if day not in series:
    issueCode, message = "MISSING_CLOSE", "参考交易日缺少收盘价，不补值或跨日计算"
  elif not _isFiniteNumber(series[day]) or series[day] <= 0:
    issueCode, message = "INVALID_CLOSE", "收盘价必须为有限正数"
  elif series[day] < minimum:
    issueCode, message = "BELOW_MIN_INDEX_CLOSE", "收盘价低于研究精度过滤门槛"
  else:
    return None
  return {"code": issueCode, "indexCode": code, "date": day.isoformat(), "role": role, "message": message}


def _returnIssue(code, day, role):
  """捕获价格相除后溢出的结果，避免无限溢价进入环境规则。"""
  return {"code": "NON_FINITE_RETURN", "indexCode": code, "date": day.isoformat(), "role": role,
          "message": "收盘价计算出的涨跌幅不是有限数"}


def _isFiniteNumber(value) -> bool:
  """排除布尔值及会在转换为浮点时溢出的巨大整数。"""
  if not isinstance(value, (int, float)) or isinstance(value, bool):
    return False
  try:
    return math.isfinite(value)
  except OverflowError:
    return False


def _assessFeatures(features, signalDate, rules: EnvironmentRuleConfig):
  """复用生产判断；合成时间仅满足类型，assess_environment 不消费该时间。"""
  snapshot = MarketPremiumSnapshot(
    trade_date=signalDate,
    source=RESEARCH_SOURCE,
    collected_at=datetime.combine(signalDate, RESEARCH_OBSERVATION_TIME, tzinfo=SHANGHAI),
    **{field: features[code] for code, field in INDEX_FIELDS.items()},
  )
  return assess_environment(snapshot, config=rules)


def _summarize(values) -> dict[str, Any]:
  """统计百分数结果；下十分位按 (N-1)*0.1 线性插值，单样本不外推。"""
  if not values:
    return {"sampleCount": 0, **dict.fromkeys(STATISTIC_FIELDS)}
  ordered = sorted(values)
  quantilePosition = (len(ordered) - 1) * LOWER_QUANTILE
  lowerIndex = math.floor(quantilePosition)
  upperIndex = math.ceil(quantilePosition)
  fraction = quantilePosition - lowerIndex
  lowerDecile = ordered[lowerIndex] * (1 - fraction) + ordered[upperIndex] * fraction
  # mean 使用稳健求和，避免偶数样本的两个有限大数相加后溢出。
  middleValues = ordered[(len(ordered) - 1) // 2:len(ordered) // 2 + 1]
  return {
    "sampleCount": len(values),
    "meanReturnPct": mean(values),
    "medianReturnPct": mean(middleValues),
    "positiveReturnRatePct": sum(value > 0 for value in values) / len(values) * PERCENT_SCALE,
    "lowerDecileReturnPct": lowerDecile,
    "worstReturnPct": ordered[0],
  }
