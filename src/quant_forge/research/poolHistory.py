"""离线校验历史涨停、炸板池请求与分页证据，不推导历史收益或证券状态。"""

# 项目研究工具模块采用小驼峰文件名。
# ruff: noqa: N999

import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

SUPPORTED_POOLS = ("limit-up-pool", "limit-break-pool")
POOL_PATH_PREFIX = "/api/a-share/special-data/"
PAGE_SIZE = 200
MAX_PAGES = 40
MAX_ROWS = PAGE_SIZE * MAX_PAGES
MILLISECONDS_PER_DAY = 86_400_000
MILLISECONDS_PER_SECOND = 1000
SHANGHAI = ZoneInfo("Asia/Shanghai")
EPOCH_DATE = date(1970, 1, 1)
CODE_PATTERN = re.compile(r"[0-9]{6}\.(?:SH|SZ|BJ)")


def requireObject(value, location):
  """要求证据节点是字典，避免把缺失字段解释为成功空响应。"""
  if not isinstance(value, dict):
    # 对外契约统一使用 ValueError 表示证据无法核验，包括结构类型错误。
    raise ValueError(f"历史池证据：{location} 必须为对象")  # noqa: TRY004
  return value


def requireInteger(value, location, minimum, maximum):
  """拒绝布尔数、文本数字、浮点及超出边界的整数。"""
  if type(value) is not int or not minimum <= value <= maximum:
    raise ValueError(f"历史池证据：{location} 必须为 {minimum} 至 {maximum} 范围内的整数")
  return value


def validateArguments(pool, day, expectedPage):
  """校验调用参数，按上海历史时区返回请求日零点的精确整数毫秒。"""
  if type(pool) is not str or pool not in SUPPORTED_POOLS:
    raise ValueError("历史池证据：pool 只支持涨停池 limit-up-pool 或炸板池 limit-break-pool")
  if type(day) is not date:
    raise ValueError("历史池证据：day 必须为 date 日期，不能使用文本或 datetime 时刻")
  requireInteger(expectedPage, "expectedPage", 1, MAX_PAGES)
  localMidnight = datetime.combine(day, time.min, tzinfo=SHANGHAI)
  # 使用实际历史偏移和整数差值，避免固定八小时误判夏令时、浮点转换产生毫秒尾差。
  delta = (day - EPOCH_DATE) - localMidnight.utcoffset()
  return delta.days * MILLISECONDS_PER_DAY + delta.seconds * MILLISECONDS_PER_SECOND


def validatePagination(data, expectedPage):
  """检查固定页大小、总页数边界及总量与页数之间的关系。"""
  pagination = requireObject(data.get("pagination"), "response.data.pagination")
  total = requireInteger(pagination.get("total"), "pagination.total", 0, MAX_ROWS)
  pages = requireInteger(pagination.get("pages"), "pagination.pages", 0, MAX_PAGES)
  page = requireInteger(pagination.get("page"), "pagination.page", 1, MAX_PAGES)
  size = requireInteger(pagination.get("size"), "pagination.size", PAGE_SIZE, PAGE_SIZE)
  expectedPages = (total + PAGE_SIZE - 1) // PAGE_SIZE
  validPages = pages in (0, 1) if total == 0 else pages == expectedPages
  if not validPages or page != expectedPage or page > max(1, pages):
    raise ValueError("历史池证据：分页总量、总页数或页号不一致")
  return {"total": total, "pages": pages, "size": size}


def validateRows(rows, day, dateMs, expectedCount):
  """检查行数、证券唯一性和显式交易日期，不把采集 timestamp 视为交易日。"""
  if not isinstance(rows, list) or len(rows) != expectedCount:
    raise ValueError("历史池证据：response.data.item 必须为列表，且行数必须与该页声明数量一致")
  seenCodes = set()
  result = []
  for row in rows:
    requireObject(row, "response.data.item 中的记录")
    code = row.get("thscode")
    if not isinstance(code, str) or not CODE_PATTERN.fullmatch(code):
      raise ValueError("历史池证据：thscode 必须为六位数字加 .SH、.SZ 或 .BJ")
    if code in seenCodes:
      raise ValueError(f"历史池证据：同一页证券代码重复：{code}")
    seenCodes.add(code)
    if "trade_date" in row and row["trade_date"] != day.isoformat():
      raise ValueError("历史池证据：显式 trade_date 必须为与请求日一致的 YYYY-MM-DD 文本")
    if "date_ms" in row and (type(row["date_ms"]) is not int or row["date_ms"] != dateMs):
      raise ValueError("历史池证据：显式 date_ms 必须为请求日上海零点的整数毫秒")
    result.append(dict(row))
  return result


def validatePoolPage(record, pool: str, day: date, expectedPage: int):
  """校验单页证据，返回 rows、total、pages、size；类型或契约不符时抛出中文 ValueError。"""
  dateMs = validateArguments(pool, day, expectedPage)
  requireObject(record, "record")
  if record.get("path") != POOL_PATH_PREFIX + pool:
    raise ValueError("历史池证据：请求 path 与指定 pool 不一致")
  params = requireObject(record.get("params"), "params")
  if type(params.get("date_ms")) is not int or params["date_ms"] != dateMs:
    raise ValueError("历史池证据：params.date_ms 必须为请求日上海零点的整数毫秒")
  page = requireInteger(params.get("page"), "params.page", 1, MAX_PAGES)
  requireInteger(params.get("size"), "params.size", PAGE_SIZE, PAGE_SIZE)
  if page != expectedPage:
    raise ValueError("历史池证据：请求页号与 expectedPage 不一致")
  response = requireObject(record.get("response"), "response")
  if type(response.get("code")) is not int or response["code"] != 0:
    raise ValueError("历史池证据：response.code 必须为整数 0，业务错误不能作为空池")
  data = requireObject(response.get("data"), "response.data")
  pagination = validatePagination(data, expectedPage)
  # 末页不足一整页时保留其真实长度；空池仍必须存在明确的第一页响应。
  expectedCount = min(PAGE_SIZE, max(0, pagination["total"] - (expectedPage - 1) * PAGE_SIZE))
  rows = validateRows(data.get("item"), day, dateMs, expectedCount)
  return {"rows": rows, **pagination}


def validatePoolArtifact(artifact, pool: str, day: date):
  """校验全部有序分页并合并 rows；empty 仅指完整空响应，不证明当天没有事件。"""
  requireObject(artifact, "artifact")
  records = artifact.get("records")
  if not isinstance(records, list) or not 1 <= len(records) <= MAX_PAGES:
    raise ValueError("历史池证据：records 必须保留 1 至 40 页响应，空池也必须有第一页")
  first = validatePoolPage(records[0], pool, day, 1)
  if len(records) != max(1, first["pages"]):
    raise ValueError("历史池证据：记录页数与声明总页数不一致，存在缺页或多余页")
  rows = list(first["rows"])
  seenCodes = {row["thscode"] for row in rows}
  for expectedPage, record in enumerate(records[1:], start=2):
    page = validatePoolPage(record, pool, day, expectedPage)
    if any(page[field] != first[field] for field in ("total", "pages", "size")):
      raise ValueError("历史池证据：不同页的总量、总页数或分页大小发生变化")
    for row in page["rows"]:
      code = row["thscode"]
      if code in seenCodes:
        raise ValueError(f"历史池证据：不同页之间证券代码重复：{code}")
      seenCodes.add(code)
      rows.append(row)
  return {"rows": rows, "total": first["total"], "pageCount": len(records), "empty": first["total"] == 0}
