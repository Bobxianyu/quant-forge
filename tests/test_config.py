"""外部配置加载和规则注入测试。"""

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_forge.config import config_snapshot_id, load_decision_config
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


def test_config_cannot_expand_d_grade_position(tmp_path: Path) -> None:
  """外部配置不得绕过 D 级零仓位硬约束。"""
  raw = json.loads(Path("config/defaults.json").read_text(encoding="utf-8"))
  raw["positions"]["D"]["total"] = [0, 100]
  path = tmp_path / "unsafe.json"
  path.write_text(json.dumps(raw), encoding="utf-8", newline="\n")

  try:
    load_decision_config(path)
  except ValueError as error:
    assert "D 级仓位" in str(error)
  else:
    raise AssertionError("D 级非零配置必须被拒绝")


def test_config_snapshot_changes_when_content_changes() -> None:
  """即使自声明版本不变，阈值变化也必须产生不同内容摘要。"""
  config = load_decision_config(Path("config/defaults.json"))
  changed = replace(
    config,
    environment=replace(config.environment, strong_premium_threshold_pct=3.0),
  )

  assert config_snapshot_id(config) != config_snapshot_id(changed)


def test_zero_sector_normalization_divisor_is_rejected(tmp_path: Path) -> None:
  """板块归一化除数为零时必须在运行规则前拒绝配置。"""
  raw = json.loads(Path("config/defaults.json").read_text(encoding="utf-8"))
  raw["sector"]["maxLimitUpCount"] = 0
  path = tmp_path / "unsafe.json"
  path.write_text(json.dumps(raw), encoding="utf-8", newline="\n")

  try:
    load_decision_config(path)
  except ValueError as error:
    assert "归一化上限" in str(error)
  else:
    raise AssertionError("零除配置必须被拒绝")


def test_fractional_integer_config_is_rejected(tmp_path: Path) -> None:
  """整数配置不得通过静默截断小数进入规则。"""
  raw = json.loads(Path("config/defaults.json").read_text(encoding="utf-8"))
  raw["risk"]["redNewsImpactLevel"] = 4.9
  path = tmp_path / "unsafe.json"
  path.write_text(json.dumps(raw), encoding="utf-8", newline="\n")

  try:
    load_decision_config(path)
  except ValueError as error:
    assert "必须为整数" in str(error)
  else:
    raise AssertionError("小数型整数配置必须被拒绝")


def test_crowding_adjustment_cannot_exceed_ten(tmp_path: Path) -> None:
  """拥挤度贡献必须被限制在正负十分范围内。"""
  raw = json.loads(Path("config/defaults.json").read_text(encoding="utf-8"))
  raw["sector"]["crowdingMaxAdjustment"] = 11
  path = tmp_path / "unsafe.json"
  path.write_text(json.dumps(raw), encoding="utf-8", newline="\n")

  try:
    load_decision_config(path)
  except ValueError as error:
    assert "零至十" in str(error)
  else:
    raise AssertionError("超出规格的拥挤度调整必须被拒绝")
