"""风险调整后的双层仓位测试。"""

import pytest

from quant_forge.domain.models import Action, PermissionGrade, RiskLevel
from quant_forge.rules.position import decide_position


@pytest.mark.parametrize(
  ("grade", "total", "single", "action"),
  [
    (PermissionGrade.A, (50, 80), (20, 30), Action.ALLOW),
    (PermissionGrade.B, (20, 50), (10, 20), Action.CAUTIOUS),
    (PermissionGrade.C, (0, 20), (0, 10), Action.TRIAL_ONLY),
    (PermissionGrade.D, (0, 0), (0, 0), Action.FORBIDDEN),
  ],
)
def test_maps_grade_to_two_position_limits(
  grade: PermissionGrade,
  total: tuple[int, int],
  single: tuple[int, int],
  action: Action,
) -> None:
  """每个权限等级必须同时映射总仓位和单笔仓位。"""
  result = decide_position(grade, RiskLevel.NONE)

  assert (result.total.minimum, result.total.maximum) == total
  assert (result.single.minimum, result.single.maximum) == single
  assert result.action is action


def test_orange_risk_caps_grade_a_position_at_grade_c() -> None:
  """橙色风险将 A 级权限封顶为 C 级。"""
  result = decide_position(PermissionGrade.A, RiskLevel.ORANGE)

  assert result.final_grade is PermissionGrade.C
  assert result.total.maximum == 20
  assert result.single.maximum == 10


def test_red_risk_forces_zero_position() -> None:
  """红色风险无条件强制 D 级和零仓位。"""
  result = decide_position(PermissionGrade.A, RiskLevel.RED)

  assert result.final_grade is PermissionGrade.D
  assert result.total.maximum == 0
  assert result.single.maximum == 0


def test_yellow_risk_downgrades_one_level() -> None:
  """黄色风险将基础权限下调一级。"""
  result = decide_position(PermissionGrade.B, RiskLevel.YELLOW)

  assert result.final_grade is PermissionGrade.C


def test_positive_adjustment_cannot_upgrade_grade_d() -> None:
  """正面因素不能把明确退潮的 D 级升级。"""
  result = decide_position(PermissionGrade.D, RiskLevel.NONE, positive_adjustment=True)

  assert result.final_grade is PermissionGrade.D


def test_positive_adjustment_can_upgrade_grade_c_to_b() -> None:
  """无风险时，高可信正面因素最多把 C 级提升到 B 级。"""
  result = decide_position(PermissionGrade.C, RiskLevel.NONE, positive_adjustment=True)

  assert result.final_grade is PermissionGrade.B
