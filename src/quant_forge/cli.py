"""本地生成盘前结构化报告和中文报告的命令入口。"""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_forge.adapters.csv_adapter import (
  DataValidationError,
  load_external_markets,
  load_market_premium,
  load_news_events,
  load_sectors,
)
from quant_forge.pipeline import build_pre_market_decision
from quant_forge.reporting.markdown_report import decision_to_dict, render_markdown

CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")
DEFAULT_CUTOFF_TIME = "08:50:00"
JSON_REPORT_NAME = "pre-market-decision.json"
MARKDOWN_REPORT_NAME = "pre-market-decision.md"


def main(arguments: Sequence[str] | None = None) -> int:
  """解析命令参数；成功返回零，数据或文件错误返回一。"""
  parser = _build_parser()
  parsed = parser.parse_args(arguments)
  if parsed.command != "run":
    parser.print_help()
    return 1

  try:
    _run_report(
      input_dir=parsed.input_dir,
      output_dir=parsed.output_dir,
      report_date=parsed.report_date,
      cutoff_time=parsed.cutoff_time,
    )
  except (DataValidationError, OSError, ValueError) as error:
    print(f"生成盘前报告失败：{error}", file=sys.stderr)
    return 1
  return 0


def _build_parser() -> argparse.ArgumentParser:
  """创建只包含当前第一阶段能力的命令解析器。"""
  parser = argparse.ArgumentParser(description="A 股盘前环境与仓位决策工具")
  subparsers = parser.add_subparsers(dest="command")
  run_parser = subparsers.add_parser("run", help="从标准 CSV 生成盘前报告")
  run_parser.add_argument("--input-dir", type=Path, required=True, help="四类标准 CSV 所在目录")
  run_parser.add_argument("--output-dir", type=Path, required=True, help="报告输出目录")
  run_parser.add_argument(
    "--report-date",
    type=date.fromisoformat,
    default=datetime.now(CHINA_TIMEZONE).date(),
    help="报告日期 YYYY-MM-DD",
  )
  run_parser.add_argument("--cutoff-time", default=DEFAULT_CUTOFF_TIME, help="正式报告截止时间 HH:MM[:SS]")
  return parser


def _run_report(
  *,
  input_dir: Path,
  output_dir: Path,
  report_date: date,
  cutoff_time: str,
) -> None:
  """加载约定文件、运行规则管道并写出两种报告。"""
  if not input_dir.is_dir():
    raise ValueError(f"输入目录不存在：{input_dir}")

  cutoff = datetime.combine(report_date, time.fromisoformat(cutoff_time), tzinfo=CHINA_TIMEZONE)
  decision = build_pre_market_decision(
    report_date=report_date,
    generated_at=datetime.now(CHINA_TIMEZONE),
    cutoff_at=cutoff,
    market_premium=load_market_premium(input_dir / "market-premium.csv"),
    external_markets=load_external_markets(input_dir / "external-markets.csv"),
    news_events=load_news_events(input_dir / "news-events.csv"),
    sectors=load_sectors(input_dir / "sectors.csv"),
  )

  output_dir.mkdir(parents=True, exist_ok=True)
  json_content = json.dumps(decision_to_dict(decision), ensure_ascii=False, indent=2) + "\n"
  _write_text(output_dir / JSON_REPORT_NAME, json_content)
  _write_text(output_dir / MARKDOWN_REPORT_NAME, render_markdown(decision))


def _write_text(path: Path, content: str) -> None:
  """统一使用 UTF-8 无 BOM 和 LF 写出报告。"""
  with path.open("w", encoding="utf-8", newline="\n") as target:
    target.write(content)


if __name__ == "__main__":
  raise SystemExit(main())
