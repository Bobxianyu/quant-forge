"""本地命令行端到端测试。"""

import json
import shutil
from pathlib import Path

from quant_forge.cli import main


def test_cli_generates_json_and_markdown_reports(tmp_path: Path) -> None:
  """示例数据应一次生成结构化和中文两种报告。"""
  exit_code = main(
    [
      "run",
      "--input-dir",
      "data/examples",
      "--output-dir",
      str(tmp_path),
      "--report-date",
      "2026-09-08",
      "--previous-trade-date",
      "2026-09-07",
      "--publish",
    ],
  )

  json_path = tmp_path / "pre-market-decision.json"
  markdown_path = tmp_path / "pre-market-decision.md"
  assert exit_code == 0
  assert json_path.exists()
  assert markdown_path.exists()
  assert json.loads(json_path.read_text(encoding="utf-8"))["position"]["final_grade"] == "A"
  assert "赚钱效应" in markdown_path.read_text(encoding="utf-8")
  assert len(list((tmp_path / "history" / "2026-09-08").iterdir())) == 1
  history_run = next((tmp_path / "history" / "2026-09-08").iterdir())
  assert (history_run / "decision-config.json").exists()
  assert (history_run / "inputs" / "market-premium.csv").exists()


def test_cli_returns_nonzero_for_missing_input_directory(tmp_path: Path, capsys) -> None:
  """输入目录不存在时返回非零状态并输出中文原因。"""
  exit_code = main(
    [
      "run",
      "--input-dir",
      str(tmp_path / "missing"),
      "--output-dir",
      str(tmp_path / "output"),
      "--report-date",
      "2026-09-08",
      "--previous-trade-date",
      "2026-09-07",
      "--publish",
    ],
  )

  captured = capsys.readouterr()
  assert exit_code == 1
  assert "生成盘前报告失败" in captured.err


def test_cli_missing_core_file_writes_zero_position_report(tmp_path: Path) -> None:
  """核心文件缺失时仍应落一份 D 级零仓位安全报告。"""
  input_dir = tmp_path / "input"
  input_dir.mkdir()
  exit_code = main(
    [
      "run",
      "--input-dir",
      str(input_dir),
      "--output-dir",
      str(tmp_path / "output"),
      "--report-date",
      "2026-09-08",
      "--previous-trade-date",
      "2026-09-07",
      "--publish",
    ],
  )

  result = json.loads((tmp_path / "output" / "pre-market-decision.json").read_text(encoding="utf-8"))
  assert exit_code == 0
  assert result["status"] == "INSUFFICIENT_DATA"
  assert result["position"]["total"]["maximum"] == 0


def test_cli_missing_non_core_files_keeps_environment_with_lower_confidence(tmp_path: Path) -> None:
  """非核心文件缺失时保留环境判断，但必须显式降低可信度。"""
  input_dir = tmp_path / "input"
  input_dir.mkdir()
  shutil.copyfile("data/examples/market-premium.csv", input_dir / "market-premium.csv")
  exit_code = main(
    [
      "run",
      "--input-dir",
      str(input_dir),
      "--output-dir",
      str(tmp_path / "output"),
      "--report-date",
      "2026-09-08",
      "--previous-trade-date",
      "2026-09-07",
      "--publish",
    ],
  )

  result = json.loads((tmp_path / "output" / "pre-market-decision.json").read_text(encoding="utf-8"))
  assert exit_code == 0
  assert result["status"] == "READY"
  assert result["confidence"] < 1


def test_cli_invalid_config_publishes_zero_position_report(tmp_path: Path) -> None:
  """非法外部配置不得留下旧交易许可，发布时应覆盖为 D 级零仓位。"""
  raw = json.loads(Path("config/defaults.json").read_text(encoding="utf-8"))
  raw["positions"]["D"]["total"] = [0, 100]
  config_path = tmp_path / "unsafe.json"
  config_path.write_text(json.dumps(raw), encoding="utf-8", newline="\n")

  exit_code = main(
    [
      "run",
      "--input-dir",
      "data/examples",
      "--output-dir",
      str(tmp_path / "output"),
      "--report-date",
      "2026-09-08",
      "--previous-trade-date",
      "2026-09-07",
      "--config",
      str(config_path),
      "--publish",
    ],
  )

  result = json.loads((tmp_path / "output" / "pre-market-decision.json").read_text(encoding="utf-8"))
  assert exit_code == 0
  assert result["status"] == "INSUFFICIENT_DATA"
  assert result["position"]["total"]["maximum"] == 0
