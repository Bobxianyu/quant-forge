"""根据四项短线溢价指标判断环境、风格和基础权限。"""

from quant_forge.config import EnvironmentRuleConfig, load_default_config
from quant_forge.domain.models import (
  EnvironmentAssessment,
  MarketEnvironment,
  MarketPremiumSnapshot,
  MarketStyle,
  PermissionGrade,
  RecoveryStrength,
  RuleEvidence,
)


def assess_environment(
  snapshot: MarketPremiumSnapshot,
  config: EnvironmentRuleConfig | None = None,
) -> EnvironmentAssessment:
  """按照固定优先级生成可解释的基础环境判断。"""
  rules = config or load_default_config().environment
  values = _premium_values(snapshot)
  positive_count = sum(value > 0 for value in values.values())
  all_negative = all(value < 0 for value in values.values())

  environment, recovery_strength, environment_rule = _classify_environment(
    snapshot,
    positive_count,
    all_negative,
  )
  market_style, style_rule = _classify_style(snapshot, all_negative, rules)
  permission_grade, permission_rule = _classify_permission(snapshot, all_negative, rules)
  actual_values = tuple((name, f"{value:.2f}%") for name, value in values.items())
  evidence = (
    RuleEvidence(
      rule_id=environment_rule,
      description="根据四项短线溢价判断全市场环境",
      actual_values=actual_values,
      effect=f"市场环境={environment.value}",
      thresholds=_environment_thresholds(environment),
    ),
    RuleEvidence(
      rule_id=style_rule,
      description="根据首板、二板和多板相对强弱判断市场风格",
      actual_values=actual_values,
      effect=f"市场风格={market_style.value}",
      thresholds=_style_thresholds(market_style, rules),
    ),
    RuleEvidence(
      rule_id=permission_rule,
      description="根据首板模式匹配度生成基础开仓权限",
      actual_values=actual_values,
      effect=f"基础权限={permission_grade.value}",
      thresholds=_permission_thresholds(permission_grade, rules),
    ),
  )

  return EnvironmentAssessment(
    environment=environment,
    recovery_strength=recovery_strength,
    market_style=market_style,
    permission_grade=permission_grade,
    evidence=evidence,
  )


def _premium_values(snapshot: MarketPremiumSnapshot) -> dict[str, float]:
  """使用业务约定的 F、S、M、L 名称组织四项指标。"""
  return {
    "F": snapshot.first_board_premium_pct,
    "S": snapshot.second_board_premium_pct,
    "M": snapshot.multi_board_premium_pct,
    "L": snapshot.limit_up_premium_pct,
  }


def _environment_thresholds(environment: MarketEnvironment) -> tuple[tuple[str, str], ...]:
  """返回命中环境分支的精确比较条件。"""
  if environment is MarketEnvironment.EBB_TIDE:
    return (("F/S/M/L", "全部 < 0%"),)
  if environment is MarketEnvironment.PROFIT_EFFECT:
    return (("positivePremiumCount", "至少 3"), ("L", "> 0%"))
  return (("classification", "不满足赚钱效应或全面退潮"),)


def _style_thresholds(
  style: MarketStyle,
  config: EnvironmentRuleConfig,
) -> tuple[tuple[str, str], ...]:
  """返回命中风格分支的精确比较条件。"""
  strong = f"{config.strong_premium_threshold_pct:.2f}%"
  conditions = {
    MarketStyle.BROAD_EBB_TIDE: (("F/S/M/L", "全部 < 0%"),),
    MarketStyle.HIGH_LOW_SWITCH: (("F", f"> {strong}"), ("S", "< 0%"), ("M", "< 0%")),
    MarketStyle.RELAY: (("S", f"> {strong}"), ("M", f"> {strong}"), ("F", f"<= {strong}")),
    MarketStyle.FIRST_BOARD_ARBITRAGE: (("F", f"> {strong}"), ("L", "> 0%")),
    MarketStyle.MIXED: (("classification", "不满足其他风格分支"),),
  }
  return conditions[style]


def _permission_thresholds(
  grade: PermissionGrade,
  config: EnvironmentRuleConfig,
) -> tuple[tuple[str, str], ...]:
  """返回命中基础权限分支的精确比较条件。"""
  strong = f"{config.strong_premium_threshold_pct:.2f}%"
  conditions = {
    PermissionGrade.A: (("F", f"> {strong}"), ("L", f"> {strong}"), ("M", ">= 0%")),
    PermissionGrade.B: (("F", f"0%～{strong}"), ("L", "> 0%")),
    PermissionGrade.C: (("classification", "不满足 A/B/D"),),
    PermissionGrade.D: (("F/S/M/L", "全部 < 0%"),),
  }
  return conditions[grade]


def _classify_environment(
  snapshot: MarketPremiumSnapshot,
  positive_count: int,
  all_negative: bool,
) -> tuple[MarketEnvironment, RecoveryStrength | None, str]:
  """先识别全面退潮，再识别赚钱效应，其他组合归入修复。"""
  if all_negative:
    return MarketEnvironment.EBB_TIDE, None, "ENV-EBB-001"
  if snapshot.limit_up_premium_pct > 0 and positive_count >= 3:
    return MarketEnvironment.PROFIT_EFFECT, None, "ENV-PROFIT-001"

  strength = RecoveryStrength.STRONG if positive_count >= 2 else RecoveryStrength.WEAK
  return MarketEnvironment.RECOVERY, strength, "ENV-RECOVERY-001"


def _classify_style(
  snapshot: MarketPremiumSnapshot,
  all_negative: bool,
  config: EnvironmentRuleConfig,
) -> tuple[MarketStyle, str]:
  """按照全面退潮、高低切、接力、首板套利、混合的顺序判断风格。"""
  if all_negative:
    return MarketStyle.BROAD_EBB_TIDE, "STYLE-EBB-001"
  if (
    snapshot.first_board_premium_pct > config.strong_premium_threshold_pct
    and snapshot.second_board_premium_pct < 0
    and snapshot.multi_board_premium_pct < 0
  ):
    return MarketStyle.HIGH_LOW_SWITCH, "STYLE-HIGH-LOW-001"
  if (
    snapshot.second_board_premium_pct > config.strong_premium_threshold_pct
    and snapshot.multi_board_premium_pct > config.strong_premium_threshold_pct
    and snapshot.first_board_premium_pct <= config.strong_premium_threshold_pct
  ):
    return MarketStyle.RELAY, "STYLE-RELAY-001"
  if (
    snapshot.first_board_premium_pct > config.strong_premium_threshold_pct
    and snapshot.limit_up_premium_pct > 0
  ):
    return MarketStyle.FIRST_BOARD_ARBITRAGE, "STYLE-FIRST-001"
  return MarketStyle.MIXED, "STYLE-MIXED-001"


def _classify_permission(
  snapshot: MarketPremiumSnapshot,
  all_negative: bool,
  config: EnvironmentRuleConfig,
) -> tuple[PermissionGrade, str]:
  """生成只针对首板模式的 A 至 D 基础权限。"""
  if all_negative:
    return PermissionGrade.D, "PERMISSION-D-001"
  if (
    snapshot.first_board_premium_pct > config.strong_premium_threshold_pct
    and snapshot.limit_up_premium_pct > config.strong_premium_threshold_pct
    and snapshot.multi_board_premium_pct >= 0
  ):
    return PermissionGrade.A, "PERMISSION-A-001"
  if (
    0 <= snapshot.first_board_premium_pct <= config.strong_premium_threshold_pct
    and snapshot.limit_up_premium_pct > 0
  ):
    return PermissionGrade.B, "PERMISSION-B-001"
  return PermissionGrade.C, "PERMISSION-C-001"
