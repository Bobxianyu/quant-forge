"""将基础权限和风险闸门转换为双层仓位限制。"""

from quant_forge.domain.models import Action, PermissionGrade, PositionDecision, PositionRange, RiskLevel, RuleEvidence

GRADE_ORDER = (
  PermissionGrade.A,
  PermissionGrade.B,
  PermissionGrade.C,
  PermissionGrade.D,
)
POSITION_LIMITS = {
  PermissionGrade.A: (Action.ALLOW, PositionRange(50, 80), PositionRange(20, 30)),
  PermissionGrade.B: (Action.CAUTIOUS, PositionRange(20, 50), PositionRange(10, 20)),
  PermissionGrade.C: (Action.TRIAL_ONLY, PositionRange(0, 20), PositionRange(0, 10)),
  PermissionGrade.D: (Action.FORBIDDEN, PositionRange(0, 0), PositionRange(0, 0)),
}


def decide_position(
  base_grade: PermissionGrade,
  risk_level: RiskLevel,
  *,
  positive_adjustment: bool = False,
) -> PositionDecision:
  """先应用有限正向修正，再让风险闸门强制降级或封顶。"""
  adjusted_grade = _apply_positive_adjustment(base_grade, risk_level, positive_adjustment)
  final_grade = _apply_risk_gate(adjusted_grade, risk_level)
  action, total, single = POSITION_LIMITS[final_grade]
  evidence = RuleEvidence(
    rule_id=f"POSITION-{risk_level.value}-001",
    description="基础权限经外围和新闻风险闸门调整",
    actual_values=(("baseGrade", base_grade.value), ("riskLevel", risk_level.value)),
    effect=f"最终权限={final_grade.value}",
  )
  return PositionDecision(base_grade, final_grade, action, total, single, (evidence,))


def _apply_positive_adjustment(
  base_grade: PermissionGrade,
  risk_level: RiskLevel,
  positive_adjustment: bool,
) -> PermissionGrade:
  """正面因素仅在无风险时允许把 C 级提升为 B 级。"""
  if positive_adjustment and risk_level is RiskLevel.NONE and base_grade is PermissionGrade.C:
    return PermissionGrade.B
  return base_grade


def _apply_risk_gate(grade: PermissionGrade, risk_level: RiskLevel) -> PermissionGrade:
  """风险调整优先于基础权限，不允许任何风险被正向因素抵消。"""
  if risk_level is RiskLevel.RED:
    return PermissionGrade.D
  if risk_level is RiskLevel.ORANGE:
    return max(grade, PermissionGrade.C, key=GRADE_ORDER.index)
  if risk_level is RiskLevel.YELLOW:
    current_index = GRADE_ORDER.index(grade)
    return GRADE_ORDER[min(current_index + 1, len(GRADE_ORDER) - 1)]
  return grade
