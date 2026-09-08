"""从版本化 JSON 加载全部可校准规则参数。"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class EnvironmentRuleConfig:
  """市场环境和风格阈值。"""

  strong_premium_threshold_pct: float


@dataclass(frozen=True, slots=True)
class RiskRuleConfig:
  """外围标的和风险闸门阈值。"""

  us_index_symbols: frozenset[str]
  a50_symbols: frozenset[str]
  red_news_impact_level: int
  red_news_confidence: float
  red_news_source_count: int
  orange_news_impact_level: int
  orange_news_confidence: float
  orange_news_source_count: int
  yellow_news_impact_level: int
  yellow_news_confidence: float
  orange_us_index_drop_pct: float
  orange_a50_drop_pct: float
  yellow_us_index_drop_pct: float
  yellow_a50_drop_pct: float

  @property
  def expected_symbols(self) -> frozenset[str]:
    """返回报告应覆盖的全部外围标的。"""
    return self.us_index_symbols | self.a50_symbols


@dataclass(frozen=True, slots=True)
class PositionRuleConfig:
  """A 至 D 级的账户和单笔仓位区间。"""

  grade_a_total: tuple[int, int]
  grade_a_single: tuple[int, int]
  grade_b_total: tuple[int, int]
  grade_b_single: tuple[int, int]
  grade_c_total: tuple[int, int]
  grade_c_single: tuple[int, int]
  grade_d_total: tuple[int, int]
  grade_d_single: tuple[int, int]


@dataclass(frozen=True, slots=True)
class SectorRuleConfig:
  """板块归一化、评分权重和趋势阈值。"""

  output_limit: int
  relative_strength_min_pct: float
  relative_strength_max_pct: float
  max_limit_up_count: int
  turnover_change_min_pct: float
  turnover_change_max_pct: float
  max_persistence_days: int
  continuation_persistence_days: int
  crowded_risk_threshold: float
  weak_breadth_threshold: float
  healthy_breadth_threshold: float
  strong_catalyst_threshold: float
  relative_strength_weight: float
  breadth_weight: float
  limit_up_weight: float
  turnover_weight: float
  persistence_weight: float
  catalyst_weight: float
  crowding_max_adjustment: float


@dataclass(frozen=True, slots=True)
class DecisionConfig:
  """一次盘前判断所需的完整版本化配置。"""

  version: str
  report_cutoff_time: str
  environment: EnvironmentRuleConfig
  risk: RiskRuleConfig
  positions: PositionRuleConfig
  sector: SectorRuleConfig


def load_decision_config(path: Path) -> DecisionConfig:
  """读取并验证配置；缺失或错误配置不得静默使用代码默认值。"""
  try:
    with path.open("r", encoding="utf-8") as source:
      raw = json.load(source, parse_constant=_reject_json_constant)
  except (OSError, UnicodeError, json.JSONDecodeError) as error:
    raise ValueError(f"无法读取规则配置 {path}：{error}") from error
  if not isinstance(raw, dict):
    raise TypeError("规则配置根节点必须为对象")
  version = raw.get("version")
  if not isinstance(version, str) or not version.strip():
    raise ValueError("配置版本不能为空")

  try:
    environment = _mapping(raw, "environment")
    risk = _mapping(raw, "risk")
    positions = _mapping(raw, "positions")
    sector = _mapping(raw, "sector")
    result = DecisionConfig(
      version=version,
      report_cutoff_time=str(raw["reportCutoffTime"]),
      environment=EnvironmentRuleConfig(
        strong_premium_threshold_pct=float(environment["strongPremiumThresholdPct"]),
      ),
      risk=RiskRuleConfig(
        us_index_symbols=_symbols(risk, "usIndexSymbols"),
        a50_symbols=_symbols(risk, "a50Symbols"),
        red_news_impact_level=_strict_int(risk, "redNewsImpactLevel"),
        red_news_confidence=float(risk["redNewsConfidence"]),
        red_news_source_count=_strict_int(risk, "redNewsSourceCount"),
        orange_news_impact_level=_strict_int(risk, "orangeNewsImpactLevel"),
        orange_news_confidence=float(risk["orangeNewsConfidence"]),
        orange_news_source_count=_strict_int(risk, "orangeNewsSourceCount"),
        yellow_news_impact_level=_strict_int(risk, "yellowNewsImpactLevel"),
        yellow_news_confidence=float(risk["yellowNewsConfidence"]),
        orange_us_index_drop_pct=float(risk["orangeUsIndexDropPct"]),
        orange_a50_drop_pct=float(risk["orangeA50DropPct"]),
        yellow_us_index_drop_pct=float(risk["yellowUsIndexDropPct"]),
        yellow_a50_drop_pct=float(risk["yellowA50DropPct"]),
      ),
      positions=_positions(positions),
      sector=_sector_config(sector),
    )
  except (KeyError, TypeError, ValueError) as error:
    raise ValueError(f"规则配置字段错误：{error}") from error
  _validate_config(result)
  return result


@lru_cache(maxsize=1)
def load_default_config() -> DecisionConfig:
  """读取项目根目录中的默认版本化配置。"""
  project_root = Path(__file__).resolve().parents[2]
  return load_decision_config(project_root / "config" / "defaults.json")


def config_snapshot_id(config: DecisionConfig) -> str:
  """按配置真实内容生成稳定摘要，避免仅依赖自声明版本号。"""
  content = json.dumps(
    asdict(config),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    default=_json_default,
  )
  digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
  return f"config:sha256:{digest}"


def _mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
  value = parent[key]
  if not isinstance(value, dict):
    raise TypeError(f"{key} 必须为对象")
  return value


def _symbols(parent: dict[str, Any], key: str) -> frozenset[str]:
  value = parent[key]
  if not isinstance(value, list) or not value:
    raise TypeError(f"{key} 必须为非空数组")
  symbols = frozenset(str(item).upper() for item in value if str(item).strip())
  if len(symbols) != len(value):
    raise ValueError(f"{key} 包含空值或重复标的")
  return symbols


def _positions(raw: dict[str, Any]) -> PositionRuleConfig:
  return PositionRuleConfig(
    grade_a_total=_position_range(raw, "A", "total"),
    grade_a_single=_position_range(raw, "A", "single"),
    grade_b_total=_position_range(raw, "B", "total"),
    grade_b_single=_position_range(raw, "B", "single"),
    grade_c_total=_position_range(raw, "C", "total"),
    grade_c_single=_position_range(raw, "C", "single"),
    grade_d_total=_position_range(raw, "D", "total"),
    grade_d_single=_position_range(raw, "D", "single"),
  )


def _position_range(raw: dict[str, Any], grade: str, kind: str) -> tuple[int, int]:
  grade_config = _mapping(raw, grade)
  value = grade_config[kind]
  if not isinstance(value, list) or len(value) != 2:
    raise TypeError(f"positions.{grade}.{kind} 必须包含两个整数")
  if any(type(item) is not int for item in value):
    raise TypeError(f"positions.{grade}.{kind} 必须包含两个整数")
  result = (value[0], value[1])
  if result[0] < 0 or result[1] > 100 or result[0] > result[1]:
    raise ValueError(f"positions.{grade}.{kind} 区间无效")
  return result


def _sector_config(raw: dict[str, Any]) -> SectorRuleConfig:
  return SectorRuleConfig(
    output_limit=_strict_int(raw, "outputLimit"),
    relative_strength_min_pct=float(raw["relativeStrengthMinPct"]),
    relative_strength_max_pct=float(raw["relativeStrengthMaxPct"]),
    max_limit_up_count=_strict_int(raw, "maxLimitUpCount"),
    turnover_change_min_pct=float(raw["turnoverChangeMinPct"]),
    turnover_change_max_pct=float(raw["turnoverChangeMaxPct"]),
    max_persistence_days=_strict_int(raw, "maxPersistenceDays"),
    continuation_persistence_days=_strict_int(raw, "continuationPersistenceDays"),
    crowded_risk_threshold=float(raw["crowdedRiskThreshold"]),
    weak_breadth_threshold=float(raw["weakBreadthThreshold"]),
    healthy_breadth_threshold=float(raw["healthyBreadthThreshold"]),
    strong_catalyst_threshold=float(raw["strongCatalystThreshold"]),
    relative_strength_weight=float(raw["relativeStrengthWeight"]),
    breadth_weight=float(raw["breadthWeight"]),
    limit_up_weight=float(raw["limitUpWeight"]),
    turnover_weight=float(raw["turnoverWeight"]),
    persistence_weight=float(raw["persistenceWeight"]),
    catalyst_weight=float(raw["catalystWeight"]),
    crowding_max_adjustment=float(raw["crowdingMaxAdjustment"]),
  )


def _validate_config(config: DecisionConfig) -> None:
  """校验会影响交易权限的关键配置不变量。"""
  if config.report_cutoff_time != "08:50:00":
    raise ValueError("正式报告截止时间必须为 08:50:00")
  if config.environment.strong_premium_threshold_pct <= 0:
    raise ValueError("强溢价阈值必须大于零")
  numeric_values = _numeric_config_values(config)
  if not all(math.isfinite(value) for value in numeric_values):
    raise ValueError("规则配置中的数值必须为有限数")
  if config.sector.output_limit < 0:
    raise ValueError("板块输出数量不能为负数")
  if config.sector.max_limit_up_count <= 0:
    raise ValueError("板块涨停家数归一化上限必须大于零")
  if config.positions.grade_d_total != (0, 0) or config.positions.grade_d_single != (0, 0):
    raise ValueError("D 级仓位必须固定为零")
  if config.positions.grade_c_total[1] > 20 or config.positions.grade_c_single[1] > 10:
    raise ValueError("C 级仓位不得突破总仓 20%、单笔 10% 的硬上限")
  position_ranges = (
    (config.positions.grade_a_total, config.positions.grade_a_single),
    (config.positions.grade_b_total, config.positions.grade_b_single),
    (config.positions.grade_c_total, config.positions.grade_c_single),
    (config.positions.grade_d_total, config.positions.grade_d_single),
  )
  if any(single[1] > total[1] for total, single in position_ranges):
    raise ValueError("单笔仓位上限不能超过账户总仓位上限")
  confidence_values = (
    config.risk.red_news_confidence,
    config.risk.orange_news_confidence,
    config.risk.yellow_news_confidence,
  )
  if not all(0 <= value <= 1 for value in confidence_values):
    raise ValueError("新闻可信度阈值必须位于零至一之间")
  if min(config.risk.red_news_source_count, config.risk.orange_news_source_count) < 1:
    raise ValueError("新闻来源数量阈值必须至少为一")
  if config.risk.red_news_source_count < config.risk.orange_news_source_count:
    raise ValueError("红色新闻来源数量阈值不能低于橙色阈值")
  impact_levels = (
    config.risk.red_news_impact_level,
    config.risk.orange_news_impact_level,
    config.risk.yellow_news_impact_level,
  )
  if not all(1 <= value <= 5 for value in impact_levels):
    raise ValueError("新闻影响级别阈值必须位于一至五之间")
  if impact_levels[0] < impact_levels[1] or impact_levels[1] < impact_levels[2]:
    raise ValueError("红橙黄新闻影响级别必须依次不升高")
  if confidence_values[0] < confidence_values[1] or confidence_values[1] < confidence_values[2]:
    raise ValueError("红橙黄新闻可信度必须依次不升高")
  if max(
    config.risk.orange_us_index_drop_pct,
    config.risk.orange_a50_drop_pct,
    config.risk.yellow_us_index_drop_pct,
    config.risk.yellow_a50_drop_pct,
  ) >= 0:
    raise ValueError("外围市场跌幅阈值必须小于零")
  if config.risk.orange_us_index_drop_pct > config.risk.yellow_us_index_drop_pct:
    raise ValueError("美股橙色跌幅阈值必须严于黄色阈值")
  if config.risk.orange_a50_drop_pct > config.risk.yellow_a50_drop_pct:
    raise ValueError("A50 橙色跌幅阈值必须严于黄色阈值")
  if config.sector.relative_strength_min_pct >= config.sector.relative_strength_max_pct:
    raise ValueError("板块相对强度最小值必须小于最大值")
  if config.sector.turnover_change_min_pct >= config.sector.turnover_change_max_pct:
    raise ValueError("板块成交变化最小值必须小于最大值")
  percentage_thresholds = (
    config.sector.crowded_risk_threshold,
    config.sector.weak_breadth_threshold,
    config.sector.healthy_breadth_threshold,
    config.sector.strong_catalyst_threshold,
  )
  if not all(0 <= value <= 100 for value in percentage_thresholds):
    raise ValueError("板块百分制阈值必须位于零至一百之间")
  if config.sector.crowding_max_adjustment < 0:
    raise ValueError("拥挤度最大调整值不能为负数")
  if config.sector.max_persistence_days <= 0:
    raise ValueError("最大持续天数必须大于零")
  if not 0 <= config.sector.continuation_persistence_days <= config.sector.max_persistence_days:
    raise ValueError("延续判定天数必须位于零至最大持续天数之间")
  weight_total = (
    config.sector.relative_strength_weight
    + config.sector.breadth_weight
    + config.sector.limit_up_weight
    + config.sector.turnover_weight
    + config.sector.persistence_weight
    + config.sector.catalyst_weight
  )
  if abs(weight_total - 0.90) > 1e-9:
    raise ValueError("板块基础权重之和必须为 0.90")


def _json_default(value: object) -> object:
  """把无序集合稳定转换为列表供配置摘要使用。"""
  if isinstance(value, (set, frozenset)):
    return sorted(value)
  raise TypeError(f"无法序列化配置值：{type(value).__name__}")


def _strict_int(parent: dict[str, Any], key: str) -> int:
  """拒绝布尔值和会被 int 静默截断的小数。"""
  value = parent[key]
  if type(value) is not int:
    raise TypeError(f"{key} 必须为整数")
  return value


def _reject_json_constant(value: str) -> None:
  """拒绝 JSON 标准之外的 NaN 和 Infinity。"""
  raise ValueError(f"不允许非有限常量 {value}")


def _numeric_config_values(config: DecisionConfig) -> tuple[float, ...]:
  """汇总浮点配置，统一执行有限数校验。"""
  return (
    config.environment.strong_premium_threshold_pct,
    config.risk.red_news_confidence,
    config.risk.orange_news_confidence,
    config.risk.yellow_news_confidence,
    config.risk.orange_us_index_drop_pct,
    config.risk.orange_a50_drop_pct,
    config.risk.yellow_us_index_drop_pct,
    config.risk.yellow_a50_drop_pct,
    config.sector.relative_strength_min_pct,
    config.sector.relative_strength_max_pct,
    config.sector.turnover_change_min_pct,
    config.sector.turnover_change_max_pct,
    config.sector.crowded_risk_threshold,
    config.sector.weak_breadth_threshold,
    config.sector.healthy_breadth_threshold,
    config.sector.strong_catalyst_threshold,
    config.sector.relative_strength_weight,
    config.sector.breadth_weight,
    config.sector.limit_up_weight,
    config.sector.turnover_weight,
    config.sector.persistence_weight,
    config.sector.catalyst_weight,
    config.sector.crowding_max_adjustment,
  )
