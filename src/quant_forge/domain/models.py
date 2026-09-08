"""盘前环境、风险、仓位和板块领域对象。"""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class MarketEnvironment(str, Enum):
  """全市场短线环境。"""

  PROFIT_EFFECT = "PROFIT_EFFECT"
  RECOVERY = "RECOVERY"
  EBB_TIDE = "EBB_TIDE"


class RecoveryStrength(str, Enum):
  """修复环境强弱。"""

  STRONG = "STRONG"
  WEAK = "WEAK"


class MarketStyle(str, Enum):
  """短线市场当前奖励的交易风格。"""

  FIRST_BOARD_ARBITRAGE = "FIRST_BOARD_ARBITRAGE"
  HIGH_LOW_SWITCH = "HIGH_LOW_SWITCH"
  RELAY = "RELAY"
  MIXED = "MIXED"
  BROAD_EBB_TIDE = "BROAD_EBB_TIDE"


class PermissionGrade(str, Enum):
  """开仓权限等级。"""

  A = "A"
  B = "B"
  C = "C"
  D = "D"


class Action(str, Enum):
  """当日交易行动。"""

  ALLOW = "ALLOW"
  CAUTIOUS = "CAUTIOUS"
  TRIAL_ONLY = "TRIAL_ONLY"
  FORBIDDEN = "FORBIDDEN"


class RiskLevel(str, Enum):
  """盘前风险闸门等级。"""

  RED = "RED"
  ORANGE = "ORANGE"
  YELLOW = "YELLOW"
  NONE = "NONE"


class SessionStatus(str, Enum):
  """外围行情交易时段状态。"""

  CLOSED = "CLOSED"
  PARTIAL = "PARTIAL"
  STALE = "STALE"


class SectorTrend(str, Enum):
  """板块盘前趋势判断。"""

  CONTINUATION = "CONTINUATION"
  POSSIBLE_STRENGTHENING = "POSSIBLE_STRENGTHENING"
  WATCH = "WATCH"
  POSSIBLE_EBB_TIDE = "POSSIBLE_EBB_TIDE"


@dataclass(frozen=True, slots=True)
class MarketPremiumSnapshot:
  """一个完整交易日形成的四项短线溢价快照。"""

  trade_date: date
  first_board_premium_pct: float
  second_board_premium_pct: float
  multi_board_premium_pct: float
  limit_up_premium_pct: float
  source: str
  collected_at: datetime


@dataclass(frozen=True, slots=True)
class ExternalMarketSnapshot:
  """一个外围市场或资产的行情快照。"""

  symbol: str
  market_date: date
  return_pct: float
  session_status: SessionStatus
  source: str
  collected_at: datetime


@dataclass(frozen=True, slots=True)
class NewsEvent:
  """截止时间前已发布并完成结构化的新闻事件。"""

  event_id: str
  published_at: datetime
  title: str
  event_type: str
  sentiment: float
  impact_level: int
  confidence: float
  affected_sectors: tuple[str, ...]
  source_count: int
  is_major_risk: bool


@dataclass(frozen=True, slots=True)
class SectorSnapshot:
  """一个板块在前一交易日收盘后的量化快照。"""

  sector_code: str
  sector_name: str
  return_pct: float
  breadth_pct: float
  limit_up_count: int
  turnover_change_pct: float
  relative_strength_pct: float
  persistence_days: int
  catalyst_score: float
  crowding_risk_score: float
  trade_date: date | None = None
  collected_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RuleEvidence:
  """一条可追溯的规则证据。"""

  rule_id: str
  description: str
  actual_values: tuple[tuple[str, str], ...]
  effect: str
  thresholds: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class EnvironmentAssessment:
  """四项溢价指标产生的基础环境判断。"""

  environment: MarketEnvironment
  recovery_strength: RecoveryStrength | None
  market_style: MarketStyle
  permission_grade: PermissionGrade
  evidence: tuple[RuleEvidence, ...]


@dataclass(frozen=True, slots=True)
class RiskAssessment:
  """外围行情和新闻产生的风险判断。"""

  level: RiskLevel
  evidence: tuple[RuleEvidence, ...] = ()
  positive_factors: tuple[str, ...] = ()
  negative_factors: tuple[str, ...] = ()
  missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PositionRange:
  """百分比仓位区间。"""

  minimum: int
  maximum: int


@dataclass(frozen=True, slots=True)
class PositionDecision:
  """风险调整后的双层仓位限制。"""

  base_grade: PermissionGrade
  final_grade: PermissionGrade
  action: Action
  total: PositionRange
  single: PositionRange
  evidence: tuple[RuleEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class SectorOutlook:
  """板块分数、趋势和可解释证据。"""

  sector_code: str
  sector_name: str
  score: float
  trend: SectorTrend
  evidence: tuple[RuleEvidence, ...]


@dataclass(frozen=True, slots=True)
class PreMarketDecision:
  """单个交易日唯一的盘前决策结果。"""

  status: str
  report_date: date
  generated_at: datetime
  cutoff_at: datetime
  environment: EnvironmentAssessment | None
  risk: RiskAssessment
  position: PositionDecision
  sector_outlooks: tuple[SectorOutlook, ...]
  confidence: float
  positive_factors: tuple[str, ...] = ()
  negative_factors: tuple[str, ...] = ()
  missing_fields: tuple[str, ...] = ()
  input_snapshot_ids: tuple[str, ...] = ()
  rule_version: str = "1.0.0"
  config_version: str = "1.0.0"
  config_snapshot_id: str = ""
  metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)
