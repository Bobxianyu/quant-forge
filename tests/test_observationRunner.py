"""研究产物冻结与阶段隔离的无网络验证。"""

import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_forge.research import observationRunner as runner
from quant_forge.research.observationRunner import loadPanel, readFrozen, writeFrozen


def testFrozenEvidenceRejectsChangedJsonAndOverwrites(tmp_path):
  path = tmp_path / "result.json"
  writeFrozen(path, {"状态": "完成"})
  assert readFrozen(path) == {"状态": "完成"}
  assert b"\r" not in path.read_bytes()
  with pytest.raises(FileExistsError):
    writeFrozen(path, {"状态": "改写"})
  path.write_text(json.dumps({"状态": "已篡改"}), encoding="utf-8")
  with pytest.raises(ValueError, match="摘要"):
    readFrozen(path)


def testMissingSidecarRejected(tmp_path):
  path = tmp_path / "raw.json"
  path.write_text("{}", encoding="utf-8")
  with pytest.raises(FileNotFoundError):
    readFrozen(path)


def testLoadingTrainingCannotConsumeHoldoutOutcome(tmp_path):
  path = tmp_path / "panel.parquet"
  records = []
  for day, loss in [("2026-06-16", 1), ("2026-06-17", 999)]:
    records.append({"date": day, "signalDate": "2026-06-15", "code": "000001.SZ",
                    "downside": 1.0, "momentum": 1.0, "position": 0.5, "liquidity": 1e8,
                    "downsidePct": float(loss), "upsidePct": 2.0, "closePct": 1.0,
                    "touchEvent": None, "sealEvent": None})
  pq.write_table(pa.Table.from_pylist(records), path)
  dates = {"2026-06-16": "B", "2026-06-17": "A", "2026-06-18": None}
  training = loadPanel(path, dates, holdout=False)
  assert set(training) == {"2026-06-16"}
  assert training["2026-06-16"]["rows"][0]["outcome"]["downsidePct"] == 1
  held = loadPanel(path, dates, holdout=True)
  assert set(held) == {"2026-06-17", "2026-06-18"}
  assert held["2026-06-18"] == {"grade": None, "rows": []}


def testFullStageFlowOnSyntheticParquetAndResume(tmp_path, monkeypatch):
  """小型真实文件链路验证：构建、锁定、留出与零覆盖恢复，不作金融效果证据。"""
  calendar = [(date(2025, 8, 1) + timedelta(days=i)).isoformat() for i in range(400)]
  targets = [(start + timedelta(days=i)).isoformat()
             for start in [date(2025, 9, 17), date(2026, 3, 17), date(2026, 6, 17)] for i in range(30)]
  inputs = {"dateGrades": dict.fromkeys(targets, "B"), "implementations": [], "limitations": ["合成测试"]}
  pools = {day: {"up": set(), "break": set()} for day in calendar}
  monkeypatch.setattr(runner, "_freezeInputs", lambda root: (inputs, calendar, pools))
  dump = tmp_path / runner.DUMP_ROOT
  dump.mkdir(parents=True)
  records = []
  for codeIndex in range(22):
    for day in calendar:
      timestamp = int(datetime.combine(date.fromisoformat(day), time.min, ZoneInfo("Asia/Shanghai")).timestamp()) * 1000
      records.append({"thscode": f"{codeIndex + 1:06}.SZ", "date_ms": timestamp,
                      "open_price": 10.0, "high_price": 11.0, "low_price": 9.0 + codeIndex * 0.025,
                      "close_price": 10.0, "volume": 20_000_000.0, "turnover": 200_000_000.0})
  pq.write_table(pa.Table.from_pylist(records), dump / "daily-k.parquet")
  pq.write_table(pa.table({"thscode": pa.array([], type=pa.string()), "ex_date_ms": pa.array([], type=pa.int64())}),
                 dump / "adjustment-factors.parquet")
  output = tmp_path / "study"
  built = runner.buildStudy(tmp_path, output)
  assert built["panelRows"] == 22 * 90
  assert runner.buildStudy(tmp_path, output) == built
  chosen = runner.selectStudy(tmp_path, output)
  assert chosen["status"] == "selected"
  assert runner.selectStudy(tmp_path, output) == readFrozen(output / "selection.json")
  held = runner.evaluateHoldout(tmp_path, output)
  assert held["selected"]["metrics"]["observedDays"] == 30
  assert held["selectionSha256"] == runner.digestFile(output / "selection.json")
  assert runner.evaluateHoldout(tmp_path, output) == held
  assert held["baselines"]["universe"]["selectedCount"] == 660
  assert held["blockedDayObservations"]["observedDays"] == 0
  with (output / "panel.parquet").open("ab") as target:
    target.write(b"changed")
  with pytest.raises(ValueError, match="面板摘要"):
    runner.verifyPanel(tmp_path, output)
