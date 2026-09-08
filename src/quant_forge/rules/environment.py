"""根据四项短线溢价指标判断环境、风格和基础权限。"""

from quant_forge.domain.models import (
  EnvironmentAssessment,
  MarketEnvironment,
  MarketPremiumSnapshot,
  MarketStyle,
  PermissionGrade,
  RecoveryStrength,
  RuleEvidence,
)

STRONG_PREMIUM_THRESHOLD_PCT = 2.0


def assess_environment(snapshot: MarketPremiumSnapshot) -> EnvironmentAssessment:
  """按照固定优先级生成可解释的基础环境判断。"""
  values = _premium_values(snapshot)
  positive_count = sum(value > 0 for value in values.values())
  all_negative = all(value < 0 for value in values.values())

  environment, recovery_strength, environment_rule = _classify_environment(
    snapshot,
    positive_count,
    all_negative,
  )
  market_style, style_rule = _classify_style(snapshot, all_negative)
  permission_grade, permission_rule = _classify_permission(snapshot, all_negative)
  actual_values = tuple((name, f"{value:.2f}%") for name, value in values.items())

  evidence = (
    RuleEvidence(
      rule_id=environment_rule,
      description="根据四项短线溢价判断全市场环境",
      actual_values=actual_values,
      effect=f"市场环境={environment.value}",
    ),
    RuleEvidence(
      rule_id=style_rule,
      description="根据首板、二板和多板相对强弱判断市场风格",
      actual_values=actual_values,
      effect=f"市场风格={market_style.value}",
    ),
    RuleEvidence(
      rule_id=permission_rule,
      description="根据首板模式匹配度生成基础开仓权限",
      actual_values=actual_values,
      effect=f"基础权限={permission_grade.value}",
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
) -> tuple[MarketStyle, str]:
  """按照全面退潮、高低切、接力、首板套利、混合的顺序判断风格。"""
  if all_negative:
    return MarketStyle.BROAD_EBB_TIDE, "STYLE-EBB-001"
  if (
    snapshot.first_board_premium_pct > STRONG_PREMIUM_THRESHOLD_PCT
    and snapshot.second_board_premium_pct < 0
    and snapshot.multi_board_premium_pct < 0
  ):
    return MarketStyle.HIGH_LOW_SWITCH, "STYLE-HIGH-LOW-001"
  if (
    snapshot.second_board_premium_pct > STRONG_PREMIUM_THRESHOLD_PCT
    and snapshot.multi_board_premium_pct > STRONG_PREMIUM_THRESHOLD_PCT
    and snapshot.first_board_premium_pct <= STRONG_PREMIUM_THRESHOLD_PCT
  ):
    return MarketStyle.RELAY, "STYLE-RELAY-001"
  if (
    snapshot.first_board_premium_pct > STRONG_PREMIUM_THRESHOLD_PCT
    and snapshot.limit_up_premium_pct > 0
  ):
    return MarketStyle.FIRST_BOARD_ARBITRAGE, "STYLE-FIRST-001"
  return MarketStyle.MIXED, "STYLE-MIXED-001"


def _classify_permission(
  snapshot: MarketPremiumSnapshot,
  all_negative: bool,
) -> tuple[PermissionGrade, str]:
  """生成只针对首板模式的 A 至 D 基础权限。"""
  if all_negative:
    return PermissionGrade.D, "PERMISSION-D-001"
  if (
    snapshot.first_board_premium_pct > STRONG_PREMIUM_THRESHOLD_PCT
    and snapshot.limit_up_premium_pct > STRONG_PREMIUM_THRESHOLD_PCT
    and snapshot.multi_board_premium_pct >= 0
  ):
    return PermissionGrade.A, "PERMISSION-A-001"
  if 0 <= snapshot.first_board_premium_pct <= STRONG_PREMIUM_THRESHOLD_PCT and snapshot.limit_up_premium_pct > 0:
    return PermissionGrade.B, "PERMISSION-B-001"
  return PermissionGrade.C, "PERMISSION-C-001"
