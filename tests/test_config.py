"""外部配置加载和规则注入测试。"""

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_forge.config import load_decision_config
from quant_forge.domain.models import MarketPremiumSnapshot, PermissionGrade, RiskLevel
from quant_forge.rules.environment import assess_environment
from quant_forge.rules.position import decide_position

CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


def test_default_config_drives_environment_and_position_rules() -> None:
  """默认配置必须真实进入环境阈值和仓位映射。"""
  config = load_decision_config(Path("config/defaults.json"))
  snapshot = MarketPremiumSnapshot(
    trade_date=date(2026, 9, 7),
    first_board_premium_pct=1.5,
    second_board_premium_pct=1.0,
    multi_board_premium_pct=0.5,
    limit_up_premium_pct=1.5,
    source="test",
    collected_at=datetime(2026, 9, 8, 8, 30, tzinfo=CHINA_TIMEZONE),
  )
  relaxed_environment = replace(config.environment, strong_premium_threshold_pct=1.0)
  custom_positions = replace(
    config.positions,
    grade_a_total=(40, 60),
  )

  assessment = assess_environment(snapshot, relaxed_environment)
  position = decide_position(PermissionGrade.A, RiskLevel.NONE, custom_positions)

  assert assessment.permission_grade is PermissionGrade.A
  assert (position.total.minimum, position.total.maximum) == (40, 60)


def test_missing_config_version_is_rejected(tmp_path: Path) -> None:
  """缺少配置版本时不能生成无法回放的结论。"""
  path = tmp_path / "config.json"
  path.write_text(json.dumps({"positions": {}}), encoding="utf-8", newline="\n")

  try:
    load_decision_config(path)
  except ValueError as error:
    assert "配置版本" in str(error)
  else:
    raise AssertionError("缺少配置版本必须抛出异常")
