"""本地生成盘前结构化报告和中文报告的命令入口。"""

import argparse
import hashlib
import json
import shutil
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime, time
from pathlib import Path
from typing import TypeVar
from zoneinfo import ZoneInfo

from quant_forge.adapters.csv_adapter import (
  DataValidationError,
  load_external_markets,
  load_market_premium,
  load_news_events,
  load_sectors,
)
from quant_forge.config import load_decision_config
from quant_forge.domain.models import PreMarketDecision
from quant_forge.pipeline import build_insufficient_decision, build_pre_market_decision
from quant_forge.reporting.markdown_report import decision_to_dict, render_markdown

T = TypeVar("T")
CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "defaults.json"
JSON_REPORT_NAME = "pre-market-decision.json"
MARKDOWN_REPORT_NAME = "pre-market-decision.md"


def main(arguments: Sequence[str] | None = None) -> int:
  """解析命令参数；成功返回零，目录或配置错误返回一。"""
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
      previous_trade_date=parsed.previous_trade_date,
      config_path=parsed.config,
      publish=parsed.publish,
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
  run_parser.add_argument(
    "--previous-trade-date",
    type=date.fromisoformat,
    required=True,
    help="上一 A 股交易日 YYYY-MM-DD",
  )
  run_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="版本化规则配置")
  run_parser.add_argument("--publish", action="store_true", help="发布为报告日唯一正式版本")
  return parser


def _run_report(
  *,
  input_dir: Path,
  output_dir: Path,
  report_date: date,
  previous_trade_date: date,
  config_path: Path,
  publish: bool,
) -> None:
  """加载约定文件、运行规则管道并写出正式报告与不可覆盖历史版本。"""
  if not input_dir.is_dir():
    raise ValueError(f"输入目录不存在：{input_dir}")
  config = load_decision_config(config_path)
  cutoff = datetime.combine(report_date, time.fromisoformat(config.report_cutoff_time), tzinfo=CHINA_TIMEZONE)
  generated_at = datetime.now(CHINA_TIMEZONE)
  try:
    market_premium = load_market_premium(input_dir / "market-premium.csv")
  except DataValidationError as error:
    decision = build_insufficient_decision(
      report_date=report_date,
      generated_at=generated_at,
      cutoff_at=cutoff,
      issues=(f"marketPremium: {error}",),
      config=config,
    )
    _write_reports(
      output_dir,
      decision,
      input_dir=input_dir,
      config_path=config_path,
      publish=publish,
    )
    return

  external_markets, external_issues = _load_optional(
    input_dir / "external-markets.csv",
    load_external_markets,
    "externalMarkets",
  )
  news_events, news_issues = _load_optional(
    input_dir / "news-events.csv",
    load_news_events,
    "newsEvents",
  )
  sectors, sector_issues = _load_optional(input_dir / "sectors.csv", load_sectors, "sectors")
  decision = build_pre_market_decision(
    report_date=report_date,
    expected_market_date=previous_trade_date,
    generated_at=generated_at,
    cutoff_at=cutoff,
    market_premium=market_premium,
    external_markets=external_markets,
    news_events=news_events,
    sectors=sectors,
    config=config,
    input_issues=(*external_issues, *news_issues, *sector_issues),
  )
  _write_reports(
    output_dir,
    decision,
    input_dir=input_dir,
    config_path=config_path,
    publish=publish,
  )


def _load_optional(
  path: Path,
  loader: Callable[[Path], tuple[T, ...]],
  label: str,
) -> tuple[tuple[T, ...], tuple[str, ...]]:
  """非核心数据不可用时记录降级项并继续生成安全报告。"""
  try:
    return loader(path), ()
  except DataValidationError as error:
    return (), (f"{label}: {error}",)


def _write_reports(
  output_dir: Path,
  decision: PreMarketDecision,
  *,
  input_dir: Path,
  config_path: Path,
  publish: bool,
) -> None:
  """写正式文件，并按内容摘要保存不可覆盖的历史副本。"""
  output_dir.mkdir(parents=True, exist_ok=True)
  json_content = json.dumps(decision_to_dict(decision), ensure_ascii=False, indent=2) + "\n"
  markdown_content = render_markdown(decision)
  run_id = hashlib.sha256(json_content.encode("utf-8")).hexdigest()[:16]
  history_dir = output_dir / "history" / decision.report_date.isoformat() / run_id
  history_dir.mkdir(parents=True, exist_ok=False)
  _write_text(history_dir / JSON_REPORT_NAME, json_content)
  _write_text(history_dir / MARKDOWN_REPORT_NAME, markdown_content)
  shutil.copyfile(config_path, history_dir / "decision-config.json")
  history_input_dir = history_dir / "inputs"
  history_input_dir.mkdir()
  for file_name in (
    "market-premium.csv",
    "external-markets.csv",
    "news-events.csv",
    "sectors.csv",
  ):
    source = input_dir / file_name
    if source.is_file():
      shutil.copyfile(source, history_input_dir / file_name)
  if publish:
    _write_text(output_dir / JSON_REPORT_NAME, json_content)
    _write_text(output_dir / MARKDOWN_REPORT_NAME, markdown_content)


def _write_text(path: Path, content: str) -> None:
  """统一使用 UTF-8 无 BOM 和 LF 写出报告。"""
  with path.open("w", encoding="utf-8", newline="\n") as target:
    target.write(content)


if __name__ == "__main__":
  raise SystemExit(main())
