"""本地命令行端到端测试。"""

import json
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
    ],
  )

  json_path = tmp_path / "pre-market-decision.json"
  markdown_path = tmp_path / "pre-market-decision.md"
  assert exit_code == 0
  assert json_path.exists()
  assert markdown_path.exists()
  assert json.loads(json_path.read_text(encoding="utf-8"))["position"]["final_grade"] == "A"
  assert "赚钱效应" in markdown_path.read_text(encoding="utf-8")


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
    ],
  )

  captured = capsys.readouterr()
  assert exit_code == 1
  assert "生成盘前报告失败" in captured.err
