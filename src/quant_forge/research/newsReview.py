"""校验新闻分析记录，仅为旁路实验提供候选与审查结论。"""
# ruff: noqa: N999

import hashlib
import json
import math
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from quant_forge.domain.models import NewsEvent

MAX_AGE_HOURS = 48
EVENT_TYPES = frozenset({"宏观", "监管", "公司", "行业", "地缘", "其他"})
CANDIDATE_FIELDS = frozenset({
  "eventType", "sentiment", "impactLevel", "confidence", "affectedSectors",
  "isMajorRisk", "evidence", "rationale",
})


def candidateHash(candidate: dict) -> str:
  """返回规范 JSON 的 SHA-256；不支持的值或非有限数值会抛出 ValueError/TypeError。"""
  raw = json.dumps(candidate, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
  return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parseTime(value: str) -> datetime:
  """解析含时区的 ISO 时间；不明确的时间抛出中文 ValueError。"""
  if not isinstance(value, str):
    raise TypeError("时间必须为含时区的文本")
  try:
    result = datetime.fromisoformat(value)
  except ValueError as error:
    raise ValueError("时间格式无效") from error
  if result.tzinfo is None or result.utcoffset() is None:
    raise ValueError("时间必须包含时区")
  return result


def requireText(value: object, label: str) -> str:
  """要求非空字符串；失败抛出带字段位置的 ValueError。"""
  if not isinstance(value, str) or not value.strip():
    raise ValueError(f"{label} 必须为非空文本")
  return value


def validateCandidate(candidate: dict, sectorCodes: list) -> None:
  """校验模型输出的完整字段、类型与范围；错误抛出中文 ValueError。"""
  if not isinstance(candidate, dict) or set(candidate) != CANDIDATE_FIELDS:
    raise ValueError("候选字段缺失或包含额外字段")
  if not isinstance(sectorCodes, list) or any(not isinstance(code, str) for code in sectorCodes):
    raise ValueError("板块白名单必须为文本数组")
  if requireText(candidate["eventType"], "eventType") not in EVENT_TYPES:
    raise ValueError("eventType 不属于允许的事件类型")
  for field, minimum, maximum in (("sentiment", -1, 1), ("confidence", 0, 1), ("impactLevel", 1, 5)):
    value = candidate[field]
    if type(value) not in (float, int) or not math.isfinite(value) or not minimum <= value <= maximum:
      raise ValueError(f"{field} 类型或范围无效")
  if type(candidate["impactLevel"]) is not int or type(candidate["isMajorRisk"]) is not bool:
    raise ValueError("impactLevel 必须为整数，isMajorRisk 必须为布尔值")
  sectors = candidate["affectedSectors"]
  if not isinstance(sectors, list) or any(not isinstance(code, str) or code not in sectorCodes for code in sectors):
    raise ValueError("affectedSectors 必须来自板块白名单")
  if len(sectors) != len(set(sectors)):
    raise ValueError("affectedSectors 包含重复板块")
  if not isinstance(candidate["evidence"], list) or not candidate["evidence"]:
    raise ValueError("evidence 不能为空")
  requireText(candidate["rationale"], "rationale")


def checkEvidence(case: dict, candidate: dict, structuredAt: datetime) -> tuple[list, list[str]]:
  """核对引文及来源时间；仅验证片段存在，语义判断依赖单独审查。"""
  sources = case["sources"]
  if not isinstance(sources, list):
    raise TypeError("sources 必须为数组")
  indexed = {}
  for source in sources:
    sourceId = requireText(source["id"], "source.id")
    if sourceId in indexed:
      raise ValueError("来源 id 重复")
    indexed[sourceId] = source
  used = {}
  issues = []
  cutoff = parseTime(case["cutoffAt"])
  for evidence in candidate["evidence"]:
    sourceId = requireText(evidence["sourceId"], "evidence.sourceId")
    if sourceId not in indexed:
      raise ValueError("引文来源不存在")
    source = indexed[sourceId]
    quote = requireText(evidence["quote"], "evidence.quote")
    if quote not in requireText(source["text"], "source.text"):
      raise ValueError("引文不在已冻结材料中")
    requireText(source["originId"], "source.originId")
    url = urlsplit(requireText(source["url"], "source.url"))
    if url.scheme != "https" or not url.hostname or url.username or url.password:
      raise ValueError("来源必须为不含凭据的 HTTPS 地址")
    publishedAt = parseTime(source["publishedAt"])
    collectedAt = parseTime(source["collectedAt"])
    if not publishedAt <= collectedAt <= structuredAt <= cutoff:
      issues.append("来源发布时间、采集时间或结构化时间不满足截止顺序")
    if cutoff - publishedAt > timedelta(hours=MAX_AGE_HOURS):
      issues.append(f"旧闻超过 {MAX_AGE_HOURS} 小时，不能视为新增盘前催化")
    used[sourceId] = source
  return list(used.values()), issues


def reviewCandidate(case: dict, prediction: dict | None, review: dict | None) -> dict:
  """审查单条候选；无效输入返回 REVIEW，永不授权正式发布。

  candidateEvent 用于展示未经准入过滤的假设影响；acceptedEvent 仅在所有检查通过时存在。
  置信度是未校准模型意见，来源独立性依赖采集端提供的 originId。
  """
  result = {"status": "REVIEW", "issues": [], "sourceCount": 0, "candidateEvent": None, "acceptedEvent": None}
  issues = result["issues"]
  try:
    if prediction is None:
      raise ValueError("缺少提取结果")
    if prediction["caseId"] != case["id"]:
      raise ValueError("提取结果与样本不匹配")
    candidate = prediction["candidate"]
    validateCandidate(candidate, case["sectorCodes"])
    structuredAt = parseTime(prediction["structuredAt"])
    cutoff = parseTime(case["cutoffAt"])
    used, evidenceIssues = checkEvidence(case, candidate, structuredAt)
    issues.extend(evidenceIssues)
    # 转载即使换了网址，仍按采集端认定的原始出处去重。
    sourceCount = len({source["originId"] for source in used})
    result["sourceCount"] = sourceCount
    event = NewsEvent(
      event_id=requireText(case["id"], "case.id"),
      published_at=max(parseTime(source["publishedAt"]) for source in used),
      title=requireText(case["title"], "case.title"), event_type=candidate["eventType"],
      sentiment=candidate["sentiment"], impact_level=candidate["impactLevel"], confidence=candidate["confidence"],
      affected_sectors=tuple(candidate["affectedSectors"]), source_count=sourceCount,
      is_major_risk=candidate["isMajorRisk"],
    )
    result["candidateEvent"] = event
    if review is None:
      raise ValueError("缺少证据审查结果")
    if review["caseId"] != case["id"] or review["candidateHash"] != candidateHash(candidate):
      issues.append("审查与候选不匹配，必须重新审查")
    if review["inputHash"] != candidateHash(case):
      issues.append("审查与冻结输入不匹配，必须重新审查")
    if review["verdict"] != "accept":
      issues.append("审查员要求待核验：" + requireText(review["reason"], "review.reason"))
    requireText(review["reason"], "review.reason")
    if not structuredAt <= parseTime(review["reviewedAt"]) <= cutoff:
      issues.append("审查时间早于提取完成或晚于截止时间")
    if not issues:
      result.update(status="ACCEPTED", acceptedEvent=event)
  except (ValueError, KeyError, TypeError, OverflowError) as error:
    issues.append(f"新闻记录校验失败：{error}")
  result["issues"] = list(dict.fromkeys(issues))
  return result
