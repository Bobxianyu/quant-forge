"""从官方近期 Parquet 文件批量读取历史成交额并保存可审计证据。"""

import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from uuid import uuid4

from .fuyaoClient import BASE_URL, FuyaoError, NoRedirect, readApiKey, redact

DUMP_PATH = "/api/dump/market-dumps/daily-k-10d/download-url"
DOWNLOAD_HOST = "o.thsi.cn"
MAX_METADATA_BYTES = 1024 * 1024
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
BATCH_ROWS = 8192
MAX_ROWS = 2_000_000
TIMEOUT_SECONDS = 60
SHANGHAI = timezone(timedelta(hours=8))
CODE_PATTERN = re.compile(r"\d{6}\.(?:SH|SZ|BJ)")
COLUMNS = ["thscode", "date_ms", "currency", "interval", "adjusted", "turnover"]


class DumpHistory:
  """可选批量数据源；失败抛出中文异常供调用方回退逐股接口。"""

  def __init__(self, cacheDir, apiKey=None, opener=None):
    """指定证据文件目录，凭据在首次请求时读取。"""
    self.cacheDir = Path(cacheDir)
    self.apiKey = apiKey
    self.opener = opener or build_opener(NoRedirect()).open

  def load(self, tradeDate):
    """返回目标交易日成交额映射和脱敏证据，缺少目标日则抛出异常。"""
    try:
      day = datetime.strptime(tradeDate, "%Y-%m-%d").replace(tzinfo=SHANGHAI)
      if day.date().isoformat() != tradeDate:
        raise ValueError
    except (ValueError, TypeError):
      raise FuyaoError("批量历史：目标日期格式非法") from None
    try:
      metadata, downloadUrl = self.fetchMetadata()
      artifact, digest, byteCount = self.download(downloadUrl)
      turnovers, rowCount, dates = self.readTurnovers(artifact, tradeDate)
      collectedAt = datetime.now(UTC).isoformat()
      evidence = {"path": DUMP_PATH, "params": {}, "response": metadata,
                  "artifactPath": str(artifact.resolve()), "sha256": digest, "bytes": byteCount,
                  "rowCount": rowCount, "tradeDates": sorted(dates), "tradeDate": tradeDate,
                  "matchedCodes": len(turnovers), "collectedAt": collectedAt,
                  "currency": "CNY", "adjust": "none", "interval": "1d"}
      artifact.with_suffix(".json").write_text(json.dumps(evidence, ensure_ascii=False),
                                                encoding="utf-8", newline="\n")
      return {"turnovers": turnovers, "rawResponses": [evidence]}
    except FuyaoError:
      raise
    except (OSError, ValueError, TypeError, KeyError, OverflowError, NotImplementedError):
      # 第三方解码或网络异常可能带签名链接，不将原异常文本对外暴露。
      raise FuyaoError("批量历史：下载、解析或证据保存失败，可回退逐股历史接口") from None

  def fetchMetadata(self):
    """读取实测的 presigned_url 信封，仅在进程内保存短效链接。"""
    key = self.apiKey or readApiKey()
    request = Request(BASE_URL + DUMP_PATH, headers={"X-api-key": key})
    with self.opener(request, timeout=TIMEOUT_SECONDS) as response:
      body = response.read(MAX_METADATA_BYTES + 1)
    if len(body) > MAX_METADATA_BYTES:
      raise FuyaoError("批量历史：下载元信息超过大小上限")
    payload = json.loads(body)
    if not isinstance(payload, dict) or payload.get("code") != 0:
      raise FuyaoError("批量历史：下载元信息业务响应失败")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("presigned_url"), str):
      raise FuyaoError("批量历史：下载元信息缺少签名地址")
    url = data["presigned_url"]
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != DOWNLOAD_HOST or parsed.port not in {None, 443}
        or parsed.username or parsed.password or parsed.fragment):
      raise FuyaoError("批量历史：下载地址不在允许的 HTTPS 主机范围")
    # 证据只保留已知安全字段，避免服务未来添加敏感签名字段。
    metadata = {"code": 0, "request_id": payload.get("request_id"), "data": {
      "presigned_url": "[已脱敏]", "presigned_url_expires_at": data.get("presigned_url_expires_at"),
      "expires_in_seconds": data.get("expires_in_seconds")}}
    return redact(metadata, key), url

  def download(self, url):
    """无鉴权头流式下载，限制大小并原子保存原始 Parquet 文件。"""
    self.cacheDir.mkdir(parents=True, exist_ok=True)
    target = self.cacheDir / f"daily-k-10d-{uuid4().hex}.parquet"
    temporary = target.with_suffix(".part")
    digest = hashlib.sha256()
    size = 0
    try:
      with self.opener(Request(url), timeout=TIMEOUT_SECONDS) as response, temporary.open("wb") as stream:
        while True:
          chunk = response.read(CHUNK_BYTES)
          if not chunk:
            break
          size += len(chunk)
          if size > MAX_DOWNLOAD_BYTES:
            raise FuyaoError("批量历史：文件超过大小上限")
          stream.write(chunk)
          digest.update(chunk)
      temporary.replace(target)
    finally:
      temporary.unlink(missing_ok=True)
    return target, digest.hexdigest(), size

  def readTurnovers(self, artifact, tradeDate):
    """分批解码并验证币种、复权、周期、日期及唯一性，缺失数值不补零。"""
    try:
      from pyarrow import parquet
    except ImportError:
      raise FuyaoError("批量历史：缺少 pyarrow 依赖，请安装项目批量历史依赖") from None
    table = parquet.ParquetFile(artifact)
    if not set(COLUMNS).issubset(table.schema_arrow.names):
      raise FuyaoError("批量历史：Parquet 缺少必要字段")
    if table.metadata.num_rows > MAX_ROWS:
      raise FuyaoError("批量历史：文件行数超过安全上限")
    turnovers = {}
    keys = set()
    dates = set()
    rowCount = 0
    for batch in table.iter_batches(batch_size=BATCH_ROWS, columns=COLUMNS):
      for row in batch.to_pylist():
        rowCount += 1
        if (row["currency"], row["interval"], row["adjusted"]) != ("CNY", "1d", "none"):
          raise FuyaoError("批量历史：币种、周期或复权口径不符")
        code = row["thscode"]
        if not isinstance(code, str) or not CODE_PATTERN.fullmatch(code):
          raise FuyaoError("批量历史：证券代码格式非法")
        dateMs = row["date_ms"]
        if not isinstance(dateMs, int) or isinstance(dateMs, bool):
          raise FuyaoError("批量历史：日期时间戳格式非法")
        dateTime = datetime.fromtimestamp(dateMs / 1000, SHANGHAI)
        if any((dateTime.hour, dateTime.minute, dateTime.second, dateTime.microsecond)):
          raise FuyaoError("批量历史：日期时间戳不是上海时区零点")
        date = dateTime.date().isoformat()
        dates.add(date)
        key = (code, date)
        if key in keys:
          raise FuyaoError("批量历史：证券与交易日存在重复记录")
        keys.add(key)
        value = row["turnover"]
        validValue = (isinstance(value, (int, float)) and not isinstance(value, bool)
                      and math.isfinite(value) and value >= 0)
        if date == tradeDate and validValue:
          turnovers[code] = value
    if tradeDate not in dates:
      raise FuyaoError("批量历史：文件未覆盖目标日期")
    return turnovers, rowCount, dates
