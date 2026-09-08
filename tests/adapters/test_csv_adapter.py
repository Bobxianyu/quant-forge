"""CSV 数据契约和校验测试。"""

from pathlib import Path

import pytest

from quant_forge.adapters.csv_adapter import (
  DataValidationError,
  load_external_markets,
  load_market_premium,
  load_news_events,
  load_sectors,
)
from quant_forge.domain.models import SessionStatus


def write_csv(path: Path, content: str) -> Path:
  """以 UTF-8 和 LF 写入测试 CSV。"""
  path.write_text(content, encoding="utf-8", newline="\n")
  return path


def test_loads_four_core_premium_metrics(tmp_path: Path) -> None:
  """四项核心指标应转换为统一领域快照。"""
  path = write_csv(
    tmp_path / "market.csv",
    "tradeDate,firstBoardPremiumPct,secondBoardPremiumPct,multiBoardPremiumPct,"
    "limitUpPremiumPct,source,collectedAt\n"
    "2026-09-07,2.1,1.0,0.5,2.2,example,2026-09-08T08:30:00+08:00\n",
  )

  result = load_market_premium(path)

  assert result.trade_date.isoformat() == "2026-09-07"
  assert result.limit_up_premium_pct == 2.2
  assert result.collected_at.utcoffset().total_seconds() == 8 * 60 * 60


def test_missing_core_field_raises_chinese_error(tmp_path: Path) -> None:
  """核心字段缺失时不得静默补零。"""
  path = write_csv(tmp_path / "market.csv", "tradeDate,source\n2026-09-07,example\n")

  with pytest.raises(DataValidationError, match="缺少核心字段.*firstBoardPremiumPct"):
    load_market_premium(path)


def test_timestamp_without_timezone_is_rejected(tmp_path: Path) -> None:
  """无时区时间戳无法参与盘前截止校验。"""
  path = write_csv(
    tmp_path / "market.csv",
    "tradeDate,firstBoardPremiumPct,secondBoardPremiumPct,multiBoardPremiumPct,"
    "limitUpPremiumPct,source,collectedAt\n"
    "2026-09-07,2.1,1.0,0.5,2.2,example,2026-09-08T08:30:00\n",
  )

  with pytest.raises(DataValidationError, match="collectedAt.*必须包含时区"):
    load_market_premium(path)


@pytest.mark.parametrize("invalid_value", ["nan", "inf", "-inf"])
def test_non_finite_core_metric_is_rejected(tmp_path: Path, invalid_value: str) -> None:
  """CSV 中的非有限数不得绕过核心字段校验。"""
  path = write_csv(
    tmp_path / "market.csv",
    "tradeDate,firstBoardPremiumPct,secondBoardPremiumPct,multiBoardPremiumPct,"
    "limitUpPremiumPct,source,collectedAt\n"
    f"2026-09-07,{invalid_value},1.0,0.5,2.2,example,2026-09-08T08:30:00+08:00\n",
  )

  with pytest.raises(DataValidationError, match="firstBoardPremiumPct.*有限数值"):
    load_market_premium(path)


def test_loads_external_markets(tmp_path: Path) -> None:
  """外围市场 CSV 支持多行并解析交易时段状态。"""
  path = write_csv(
    tmp_path / "external.csv",
    "symbol,marketDate,returnPct,sessionStatus,source,collectedAt\n"
    "NASDAQ,2026-09-07,-1.2,CLOSED,example,2026-09-08T08:30:00+08:00\n"
    "A50,2026-09-08,0.3,PARTIAL,example,2026-09-08T08:30:00+08:00\n",
  )

  result = load_external_markets(path)

  assert len(result) == 2
  assert result[1].session_status is SessionStatus.PARTIAL


def test_loads_news_and_splits_affected_sectors(tmp_path: Path) -> None:
  """新闻板块列表按竖线拆分并过滤空值。"""
  path = write_csv(
    tmp_path / "news.csv",
    "eventId,publishedAt,title,eventType,sentiment,impactLevel,confidence,"
    "affectedSectors,sourceCount,isMajorRisk\n"
    "n1,2026-09-08T07:00:00+08:00,政策支持,行业,0.8,4,0.9,AI|CHIP,2,false\n",
  )

  result = load_news_events(path)

  assert result[0].affected_sectors == ("AI", "CHIP")
  assert result[0].is_major_risk is False


def test_loads_sector_snapshot(tmp_path: Path) -> None:
  """板块 CSV 应保留评分所需全部原始字段。"""
  path = write_csv(
    tmp_path / "sectors.csv",
    "sectorCode,sectorName,tradeDate,collectedAt,returnPct,breadthPct,limitUpCount,turnoverChangePct,"
    "relativeStrengthPct,persistenceDays,catalystScore,crowdingRiskScore\n"
    "AI,人工智能,2026-09-07,2026-09-08T08:30:00+08:00,2.1,70,5,20,1.5,3,80,35\n",
  )

  result = load_sectors(path)

  assert result[0].sector_name == "人工智能"
  assert result[0].catalyst_score == 80
