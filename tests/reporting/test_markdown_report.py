"""结构化和中文 Markdown 报告测试。"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from quant_forge.domain.models import MarketPremiumSnapshot
from quant_forge.pipeline import build_pre_market_decision
from quant_forge.reporting.markdown_report import decision_to_dict, render_markdown

CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


def make_decision():
  """创建最小可报告决策。"""
  report_date = date(2026, 9, 8)
  return build_pre_market_decision(
    report_date=report_date,
    generated_at=datetime(2026, 9, 8, 8, 40, tzinfo=CHINA_TIMEZONE),
    cutoff_at=datetime(2026, 9, 8, 8, 50, tzinfo=CHINA_TIMEZONE),
    market_premium=MarketPremiumSnapshot(
      trade_date=date(2026, 9, 7),
      first_board_premium_pct=2.5,
      second_board_premium_pct=1.0,
      multi_board_premium_pct=0.5,
      limit_up_premium_pct=2.2,
      source="test",
      collected_at=datetime(2026, 9, 8, 8, 30, tzinfo=CHINA_TIMEZONE),
    ),
    external_markets=(),
    news_events=(),
    sectors=(),
  )


def test_markdown_contains_actionable_summary() -> None:
  """中文报告必须展示环境、权限、仓位和截止时间。"""
  content = render_markdown(make_decision())

  assert "赚钱效应" in content
  assert "A级" in content
  assert "50%～80%" in content
  assert "20%～30%" in content
  assert "08:50" in content


def test_decision_to_dict_serializes_dates_and_enums() -> None:
  """结构化报告必须能被标准 JSON 编码器直接处理。"""
  result = decision_to_dict(make_decision())

  assert result["report_date"] == "2026-09-08"
  assert result["environment"]["environment"] == "PROFIT_EFFECT"
  assert result["position"]["final_grade"] == "A"
