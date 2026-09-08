"""结构化决策序列化和中文 Markdown 报告。"""

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from quant_forge.domain.models import MarketEnvironment, MarketStyle, PreMarketDecision, RiskLevel, SectorTrend

ENVIRONMENT_LABELS = {
  MarketEnvironment.PROFIT_EFFECT: "赚钱效应",
  MarketEnvironment.RECOVERY: "修复",
  MarketEnvironment.EBB_TIDE: "退潮",
}
STYLE_LABELS = {
  MarketStyle.FIRST_BOARD_ARBITRAGE: "首板套利",
  MarketStyle.HIGH_LOW_SWITCH: "高低切",
  MarketStyle.RELAY: "接力行情",
  MarketStyle.MIXED: "混合",
  MarketStyle.BROAD_EBB_TIDE: "全面退潮",
}
RISK_LABELS = {
  RiskLevel.RED: "红色",
  RiskLevel.ORANGE: "橙色",
  RiskLevel.YELLOW: "黄色",
  RiskLevel.NONE: "无重大风险",
}
SECTOR_TREND_LABELS = {
  SectorTrend.CONTINUATION: "延续",
  SectorTrend.POSSIBLE_STRENGTHENING: "可能转强",
  SectorTrend.WATCH: "观察",
  SectorTrend.POSSIBLE_EBB_TIDE: "可能退潮",
}


def decision_to_dict(decision: PreMarketDecision) -> dict[str, Any]:
  """把领域对象转换为可由标准 JSON 编码器处理的字典。"""
  result = _to_json_value(decision)
  if not isinstance(result, dict):
    raise TypeError("盘前决策序列化结果必须为字典")
  return result


def render_markdown(decision: PreMarketDecision) -> str:
  """生成适合盘前人工复核的中文 Markdown 报告。"""
  environment_label = "数据不足"
  style_label = "无法判断"
  if decision.environment is not None:
    environment_label = ENVIRONMENT_LABELS[decision.environment.environment]
    style_label = STYLE_LABELS[decision.environment.market_style]

  lines = [
    f"# {decision.report_date.isoformat()} A 股盘前决策",
    "",
    f"- 状态：{decision.status}",
    f"- 正式截止时间：{decision.cutoff_at:%Y-%m-%d %H:%M %z}",
    f"- 市场环境：{environment_label}",
    f"- 市场风格：{style_label}",
    f"- 开仓权限：{decision.position.final_grade.value}级",
    f"- 账户总仓位：{_format_range(decision.position.total.minimum, decision.position.total.maximum)}",
    f"- 单笔仓位：{_format_range(decision.position.single.minimum, decision.position.single.maximum)}",
    f"- 风险闸门：{RISK_LABELS[decision.risk.level]}",
    f"- 可信度：{decision.confidence:.0%}",
    "",
    "## 板块优先级",
    "",
  ]
  if decision.sector_outlooks:
    lines.extend(
      f"{index}. {item.sector_name}（{item.score:.2f} 分，{SECTOR_TREND_LABELS[item.trend]}）"
      for index, item in enumerate(decision.sector_outlooks, start=1)
    )
  else:
    lines.append("暂无可用板块结论。")

  lines.extend(
    [
      "",
      "## 利好因素",
      "",
      _format_items(decision.positive_factors),
      "",
      "## 利空因素",
      "",
      _format_items(decision.negative_factors),
      "",
      "## 数据缺失或降级项",
      "",
      _format_items(decision.missing_fields),
      "",
      f"规则版本：`{decision.rule_version}`；配置版本：`{decision.config_version}`。",
      "",
      "> 本报告仅用于量化研究和风险约束，不构成收益承诺或自动交易指令。",
      "",
    ],
  )
  return "\n".join(lines)


def _to_json_value(value: Any) -> Any:
  """递归转换数据类、枚举、日期和元组。"""
  if is_dataclass(value) and not isinstance(value, type):
    return {item.name: _to_json_value(getattr(value, item.name)) for item in fields(value)}
  if isinstance(value, Enum):
    return value.value
  if isinstance(value, (date, datetime)):
    return value.isoformat()
  if isinstance(value, tuple):
    return [_to_json_value(item) for item in value]
  if isinstance(value, dict):
    return {str(key): _to_json_value(item) for key, item in value.items()}
  return value


def _format_range(minimum: int, maximum: int) -> str:
  if minimum == maximum:
    return f"{minimum}%"
  return f"{minimum}%～{maximum}%"


def _format_items(items: tuple[str, ...]) -> str:
  if not items:
    return "无。"
  return "\n".join(f"- {item}" for item in items)
