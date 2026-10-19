"""新闻分析旁路验证的输入边界、规则影响及报告测试。"""

import copy
import json

import pytest

from quant_forge.research.newsPilot import main, runPilot
from quant_forge.research.newsReview import candidateHash, reviewCandidate


def makeRecord():
  """生成明确标注为合成的完整双轮分析记录。"""
  candidate = {
    "eventType": "宏观", "sentiment": 0.7, "impactLevel": 3, "confidence": 0.8,
    "affectedSectors": ["FIN"], "isMajorRisk": False,
    "evidence": [{"sourceId": "s1", "quote": "合成金融支持措施"}],
    "rationale": "合成措施可能支持金融板块，不能据此预测涨幅。",
  }
  case = {
    "id": "synthetic-positive", "kind": "synthetic", "title": "合成支持政策",
    "cutoffAt": "2026-09-08T08:50:00+08:00", "sectorCodes": ["FIN"],
    "sources": [{
      "id": "s1", "originId": "announcement-1", "url": "https://example.com/fixture",
      "text": "合成金融支持措施，属于程序测试材料。", "publishedAt": "2026-09-08T07:00:00+08:00",
      "collectedAt": "2026-09-08T08:00:00+08:00",
    }],
  }
  prediction = {"caseId": case["id"], "structuredAt": "2026-09-08T08:10:00+08:00", "candidate": candidate}
  review = {
    "caseId": case["id"], "candidateHash": candidateHash(candidate), "verdict": "accept",
    "inputHash": candidateHash(case),
    "reason": "合成测试审查通过。", "reviewedAt": "2026-09-08T08:20:00+08:00",
  }
  return case, prediction, review


def test_valid_event_changes_only_hypothetical_permission():
  """有效利好展示 C 到 B 的敏感性，永远不能发布正式决策。"""
  case, prediction, review = makeRecord()
  original = copy.deepcopy((case, prediction, review))
  result = runPilot([case], [prediction], [review])
  row = result["rows"][0]
  assert row["status"] == "ACCEPTED"
  assert row["baselineGrade"] == "C"
  assert row["shadowGrade"] == "B"
  assert result["formalEligible"] is False
  assert (case, prediction, review) == original


def test_reprints_do_not_satisfy_two_independent_sources():
  """相同原始公告的转载不能抬高风险级别。"""
  case, prediction, review = makeRecord()
  case["sources"].append({**case["sources"][0], "id": "s2", "url": "https://example.org/reprint"})
  candidate = prediction["candidate"]
  candidate.update(sentiment=-0.9, impactLevel=5, isMajorRisk=True)
  candidate["evidence"].append({"sourceId": "s2", "quote": "合成金融支持措施"})
  review["candidateHash"] = candidateHash(candidate)
  review["inputHash"] = candidateHash(case)
  row = runPilot([case], [prediction], [review])["rows"][0]
  assert row["sourceCount"] == 1
  assert row["shadowRisk"] == "YELLOW"
  case["sources"][1]["originId"] = "independent-announcement"
  review["inputHash"] = candidateHash(case)
  row = runPilot([case], [prediction], [review])["rows"][0]
  assert row["shadowRisk"] == "RED"
  assert row["shadowGrade"] == "D"


@pytest.mark.parametrize("field,value", [
  ("sentiment", float("nan")), ("sentiment", 2), ("confidence", True), ("confidence", 1.1),
  ("impactLevel", 1.5), ("impactLevel", 0), ("isMajorRisk", "false"),
  ("affectedSectors", ["UNKNOWN"]), ("affectedSectors", "FIN"), ("eventType", "随意类型"),
  ("evidence", []), ("evidence", [{"sourceId": "s1", "quote": "不存在的原文"}]),
  ("evidence", [{"sourceId": "missing", "quote": "合成金融支持措施"}]),
])
def test_bad_candidate_is_blocked(field, value):
  """错误类型、非有限数值、未授权板块和伪造引文必须进入待核验。"""
  case, prediction, review = makeRecord()
  prediction["candidate"][field] = value
  result = reviewCandidate(case, prediction, review)
  assert result["status"] == "REVIEW"
  assert any("校验失败" in issue for issue in result["issues"])


@pytest.mark.parametrize("target,field,value", [
  ("source", "publishedAt", "2026-09-08T08:51:00+08:00"),
  ("source", "publishedAt", "2026-09-01T07:00:00+08:00"),
  ("source", "collectedAt", "2026-09-08T08:51:00+08:00"),
  ("source", "collectedAt", "2026-09-08T06:00:00+08:00"),
  ("prediction", "structuredAt", "2026-09-08T08:51:00+08:00"),
  ("prediction", "structuredAt", "2026-09-08T07:59:00+08:00"),
  ("review", "reviewedAt", "2026-09-08T08:09:00+08:00"),
  ("review", "reviewedAt", "2026-09-08T08:51:00+08:00"),
  ("review", "verdict", "review"), ("review", "candidateHash", "tampered"),
  ("case", "cutoffAt", "2026-09-08T08:50:00"),
])
def test_time_and_review_failures_cannot_change_shadow_grade(target, field, value):
  """时间穿越、旧闻和无效审查均不得产生权限提升。"""
  case, prediction, review = makeRecord()
  objects = {"source": case["sources"][0], "prediction": prediction, "review": review, "case": case}
  objects[target][field] = value
  review["inputHash"] = candidateHash(case)
  row = runPilot([case], [prediction], [review])["rows"][0]
  assert row["status"] == "REVIEW"
  assert row["shadowGrade"] == "C"


def test_missing_prediction_and_reviewer_do_not_disappear_from_denominator():
  """缺失输出仍计入总样本并标为待核验。"""
  case, prediction, _ = makeRecord()
  report = runPilot([case], [], [])
  assert report["counts"]["total"] == 1
  assert report["counts"]["review"] == 1
  assert runPilot([case], [prediction], [])["rows"][0]["status"] == "REVIEW"


def test_duplicate_case_and_unknown_response_are_rejected():
  """歧义标识不能被字典覆盖或静默丢弃。"""
  case, prediction, review = makeRecord()
  with pytest.raises(ValueError, match="重复"):
    runPilot([case, case], [prediction], [review])
  with pytest.raises(ValueError, match="未知"):
    runPilot([case], [{**prediction, "caseId": "other"}], [review])


def test_cli_writes_only_named_pilot_artifacts(tmp_path):
  """离线入口输出可读报告，且不能覆盖正式报告。"""
  case, prediction, review = makeRecord()
  payloads = {"cases": [case], "predictions": [prediction], "reviews": [review]}
  for name, records in payloads.items():
    (tmp_path / f"{name}.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
  output = tmp_path / "result"
  args = ["--input-dir", str(tmp_path), "--output-dir", str(output)]
  assert main(args) == 0
  assert sorted(path.name for path in output.iterdir()) == ["news-pilot.json", "news-pilot.md"]
  report = json.loads((output / "news-pilot.json").read_text(encoding="utf-8"))
  assert report["modelCost"] is None
  assert "独立人工" in (output / "news-pilot.md").read_text(encoding="utf-8")
  assert main(["--input-dir", str(tmp_path / "missing"), "--output-dir", str(output)]) == 1


def test_boundary_timestamp_and_freshness_are_inclusive():
  """截止时刻以及恰好 48 小时的材料仍满足机械时间边界。"""
  case, prediction, review = makeRecord()
  case["sources"][0]["publishedAt"] = "2026-09-06T08:50:00+08:00"
  review["reviewedAt"] = case["cutoffAt"]
  review["inputHash"] = candidateHash(case)
  assert reviewCandidate(case, prediction, review)["status"] == "ACCEPTED"


def test_changed_source_requires_new_review():
  """即使引文仍存在，来源或截止时间变动也必须重新审查。"""
  case, prediction, review = makeRecord()
  case["sources"][0]["text"] += "材料后续被修改。"
  result = reviewCandidate(case, prediction, review)
  assert result["status"] == "REVIEW"
  assert any("输入" in issue for issue in result["issues"])


def test_cli_rejects_duplicate_json_keys(tmp_path):
  """文件内重复字段不能在 JSON 解析时被静默覆盖。"""
  (tmp_path / "cases.json").write_text('[{"id": "first", "id": "second"}]', encoding="utf-8")
  assert main(["--input-dir", str(tmp_path), "--output-dir", str(tmp_path / "output")]) == 1
  assert not (tmp_path / "output").exists()


def test_recorded_pilot_keeps_real_and_synthetic_results_separate():
  """真实旧材料不得冒充新催化，合成控制结果与真实样本分开计数。"""
  from pathlib import Path

  root = Path(__file__).resolve().parents[1] / "data/research/news-pilot"
  records = [json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
             for name in ("cases", "predictions", "reviews")]
  report = runPilot(*records)
  assert report["counts"] == {"total": 9, "accepted": 3, "review": 6, "real": 3, "synthetic": 6}
  realRows = [row for row in report["rows"] if row["kind"] == "real"]
  assert all(row["status"] == "REVIEW" and row["shadowGrade"] == "C" for row in realRows)
  assert report["modelAccuracy"] is None
