"""标准 CSV 数据契约读取与中文错误校验。"""

import csv
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import TypeVar

from quant_forge.domain.models import (
  ExternalMarketSnapshot,
  MarketPremiumSnapshot,
  NewsEvent,
  SectorSnapshot,
  SessionStatus,
)

T = TypeVar("T")


class DataValidationError(ValueError):
  """输入文件无法安全转换为领域对象。"""


def load_market_premium(path: Path) -> MarketPremiumSnapshot:
  """读取只包含一条记录的四项短线溢价快照。"""
  required = (
    "tradeDate",
    "firstBoardPremiumPct",
    "secondBoardPremiumPct",
    "multiBoardPremiumPct",
    "limitUpPremiumPct",
    "source",
    "collectedAt",
  )
  rows = _read_rows(path, required, core_fields=True)
  if len(rows) != 1:
    raise DataValidationError(f"{path}: 核心溢价文件必须且只能包含一条数据，实际为 {len(rows)} 条")

  line_number, row = rows[0]
  return MarketPremiumSnapshot(
    trade_date=_field(path, line_number, row, "tradeDate", date.fromisoformat),
    first_board_premium_pct=_field(path, line_number, row, "firstBoardPremiumPct", float),
    second_board_premium_pct=_field(path, line_number, row, "secondBoardPremiumPct", float),
    multi_board_premium_pct=_field(path, line_number, row, "multiBoardPremiumPct", float),
    limit_up_premium_pct=_field(path, line_number, row, "limitUpPremiumPct", float),
    source=_required_text(path, line_number, row, "source"),
    collected_at=_field(path, line_number, row, "collectedAt", _parse_datetime),
  )


def load_external_markets(path: Path) -> tuple[ExternalMarketSnapshot, ...]:
  """读取零条或多条外围市场快照。"""
  required = ("symbol", "marketDate", "returnPct", "sessionStatus", "source", "collectedAt")
  return tuple(_external_from_row(path, line_number, row) for line_number, row in _read_rows(path, required))


def load_news_events(path: Path) -> tuple[NewsEvent, ...]:
  """读取零条或多条结构化新闻事件。"""
  required = (
    "eventId",
    "publishedAt",
    "title",
    "eventType",
    "sentiment",
    "impactLevel",
    "confidence",
    "affectedSectors",
    "sourceCount",
    "isMajorRisk",
  )
  return tuple(_news_from_row(path, line_number, row) for line_number, row in _read_rows(path, required))


def load_sectors(path: Path) -> tuple[SectorSnapshot, ...]:
  """读取零条或多条板块快照。"""
  required = (
    "sectorCode",
    "sectorName",
    "returnPct",
    "breadthPct",
    "limitUpCount",
    "turnoverChangePct",
    "relativeStrengthPct",
    "persistenceDays",
    "catalystScore",
    "crowdingRiskScore",
  )
  return tuple(_sector_from_row(path, line_number, row) for line_number, row in _read_rows(path, required))


def _read_rows(
  path: Path,
  required_fields: tuple[str, ...],
  *,
  core_fields: bool = False,
) -> tuple[tuple[int, dict[str, str]], ...]:
  """读取字典行，并在转换前统一验证表头。"""
  try:
    with path.open("r", encoding="utf-8", newline="") as source:
      reader = csv.DictReader(source)
      headers = tuple(reader.fieldnames or ())
      missing = tuple(field for field in required_fields if field not in headers)
      if missing:
        label = "核心字段" if core_fields else "字段"
        raise DataValidationError(f"{path}: 缺少{label}：{', '.join(missing)}")
      return tuple((line_number, dict(row)) for line_number, row in enumerate(reader, start=2))
  except OSError as error:
    raise DataValidationError(f"{path}: 无法读取文件：{error}") from error
  except UnicodeError as error:
    raise DataValidationError(f"{path}: 文件不是有效的 UTF-8 文本") from error


def _field(
  path: Path,
  line_number: int,
  row: dict[str, str],
  field_name: str,
  converter: Callable[[str], T],
) -> T:
  """转换单个字段并补充文件、行号和字段上下文。"""
  raw_value = row.get(field_name, "").strip()
  if not raw_value:
    raise DataValidationError(f"{path}:{line_number}: 字段 {field_name} 不能为空")
  try:
    return converter(raw_value)
  except DataValidationError as error:
    raise DataValidationError(f"{path}:{line_number}: 字段 {field_name} {error}") from error
  except (TypeError, ValueError) as error:
    raise DataValidationError(
      f"{path}:{line_number}: 字段 {field_name} 的值 {raw_value!r} 格式错误",
    ) from error


def _required_text(path: Path, line_number: int, row: dict[str, str], field_name: str) -> str:
  """读取不能为空的文本字段。"""
  return _field(path, line_number, row, field_name, str)


def _parse_datetime(value: str) -> datetime:
  """解析必须携带 UTC 偏移的 ISO 8601 时间。"""
  result = datetime.fromisoformat(value)
  if result.tzinfo is None or result.utcoffset() is None:
    raise DataValidationError("必须包含时区")
  return result


def _parse_bool(value: str) -> bool:
  """严格解析布尔值，拒绝含义不明确的文本。"""
  normalized = value.lower()
  if normalized == "true":
    return True
  if normalized == "false":
    return False
  raise DataValidationError("必须为 true 或 false")


def _external_from_row(path: Path, line_number: int, row: dict[str, str]) -> ExternalMarketSnapshot:
  return ExternalMarketSnapshot(
    symbol=_required_text(path, line_number, row, "symbol"),
    market_date=_field(path, line_number, row, "marketDate", date.fromisoformat),
    return_pct=_field(path, line_number, row, "returnPct", float),
    session_status=_field(path, line_number, row, "sessionStatus", SessionStatus),
    source=_required_text(path, line_number, row, "source"),
    collected_at=_field(path, line_number, row, "collectedAt", _parse_datetime),
  )


def _news_from_row(path: Path, line_number: int, row: dict[str, str]) -> NewsEvent:
  sentiment = _field(path, line_number, row, "sentiment", float)
  confidence = _field(path, line_number, row, "confidence", float)
  impact_level = _field(path, line_number, row, "impactLevel", int)
  source_count = _field(path, line_number, row, "sourceCount", int)
  _validate_range(path, line_number, "sentiment", sentiment, -1, 1)
  _validate_range(path, line_number, "confidence", confidence, 0, 1)
  _validate_range(path, line_number, "impactLevel", impact_level, 1, 5)
  _validate_range(path, line_number, "sourceCount", source_count, 1, None)
  sectors = tuple(value.strip() for value in row.get("affectedSectors", "").split("|") if value.strip())
  return NewsEvent(
    event_id=_required_text(path, line_number, row, "eventId"),
    published_at=_field(path, line_number, row, "publishedAt", _parse_datetime),
    title=_required_text(path, line_number, row, "title"),
    event_type=_required_text(path, line_number, row, "eventType"),
    sentiment=sentiment,
    impact_level=impact_level,
    confidence=confidence,
    affected_sectors=sectors,
    source_count=source_count,
    is_major_risk=_field(path, line_number, row, "isMajorRisk", _parse_bool),
  )


def _sector_from_row(path: Path, line_number: int, row: dict[str, str]) -> SectorSnapshot:
  breadth = _field(path, line_number, row, "breadthPct", float)
  limit_up_count = _field(path, line_number, row, "limitUpCount", int)
  persistence_days = _field(path, line_number, row, "persistenceDays", int)
  catalyst = _field(path, line_number, row, "catalystScore", float)
  crowding = _field(path, line_number, row, "crowdingRiskScore", float)
  _validate_range(path, line_number, "breadthPct", breadth, 0, 100)
  _validate_range(path, line_number, "limitUpCount", limit_up_count, 0, None)
  _validate_range(path, line_number, "persistenceDays", persistence_days, 0, None)
  _validate_range(path, line_number, "catalystScore", catalyst, 0, 100)
  _validate_range(path, line_number, "crowdingRiskScore", crowding, 0, 100)
  return SectorSnapshot(
    sector_code=_required_text(path, line_number, row, "sectorCode"),
    sector_name=_required_text(path, line_number, row, "sectorName"),
    return_pct=_field(path, line_number, row, "returnPct", float),
    breadth_pct=breadth,
    limit_up_count=limit_up_count,
    turnover_change_pct=_field(path, line_number, row, "turnoverChangePct", float),
    relative_strength_pct=_field(path, line_number, row, "relativeStrengthPct", float),
    persistence_days=persistence_days,
    catalyst_score=catalyst,
    crowding_risk_score=crowding,
  )


def _validate_range(
  path: Path,
  line_number: int,
  field_name: str,
  value: float,
  minimum: float,
  maximum: float | None,
) -> None:
  """校验闭区间；最大值为空时仅校验下界。"""
  if value < minimum or (maximum is not None and value > maximum):
    upper = "无上限" if maximum is None else str(maximum)
    raise DataValidationError(
      f"{path}:{line_number}: 字段 {field_name} 必须位于 {minimum} 至 {upper}，实际为 {value}",
    )
