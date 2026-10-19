"""批量历史文件的日期、口径和下载安全测试。"""

import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from quant_forge.market.dumpHistory import DumpHistory
from quant_forge.market.fuyaoClient import FuyaoError

DATE_MS = int(datetime(2026, 9, 15, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)


def makeParquet(rows):
  """生成离线真实 Parquet 夹具。"""
  import pyarrow as pa
  import pyarrow.parquet as pq
  buffer = io.BytesIO()
  pq.write_table(pa.Table.from_pylist(rows), buffer)
  return buffer.getvalue()


def sampleRow(**changes):
  """构造官方日 K 字段。"""
  return {"thscode": "600519.SH", "date_ms": DATE_MS, "currency": "CNY", "interval": "1d",
          "adjusted": "none", "turnover": 123.0, **changes}


def makeLoader(tmp_path, rows, url="https://o.thsi.cn/file?signature=secret"):
  """分别模拟鉴权元数据与不带鉴权的文件下载。"""
  calls = []
  content = makeParquet(rows)
  def opener(request, timeout):
    calls.append(request)
    if len(calls) == 1:
      return io.BytesIO(json.dumps({"code": 0, "request_id": "test", "data": {
        "presigned_url": url, "presigned_url_expires_at": "2026-09-16T23:13:45+08:00",
        "expires_in_seconds": 300}}).encode())
    return io.BytesIO(content)
  return DumpHistory(cacheDir=tmp_path, apiKey="secret", opener=opener), calls


def testBatchAndSafeEvidence(tmp_path):
  """批量取值并留原文件、哈希证据，下载不携带 API 密钥。"""
  loader, calls = makeLoader(tmp_path, [sampleRow()])
  result = loader.load("2026-09-15")
  assert result["turnovers"] == {"600519.SH": 123.0}
  assert calls[0].get_header("X-api-key") == "secret"
  assert calls[1].get_header("X-api-key") is None
  assert "secret" not in json.dumps(result)
  assert result["rawResponses"][0]["sha256"]
  assert len(list(tmp_path.glob("*.parquet"))) == 1


@pytest.mark.parametrize("changes", [{"adjusted": "forward"}, {"currency": "USD"}, {"interval": "1w"}])
def testWrongBasisRejected(tmp_path, changes):
  """币种、周期或复权不符时整批拒绝。"""
  loader, _ = makeLoader(tmp_path, [sampleRow(**changes)])
  with pytest.raises(FuyaoError, match="口径"):
    loader.load("2026-09-15")


def testMissingDateAndDuplicateRejected(tmp_path):
  """缺少目标日期或相同证券日线重复时拒绝发布。"""
  loader, _ = makeLoader(tmp_path, [sampleRow()])
  with pytest.raises(FuyaoError, match="日期"):
    loader.load("2026-09-14")
  loader, _ = makeLoader(tmp_path, [sampleRow(), sampleRow()])
  with pytest.raises(FuyaoError, match="重复"):
    loader.load("2026-09-15")


@pytest.mark.parametrize("url", ["http://o.thsi.cn/file", "https://evil.example/file",
                                 "https://o.thsi.cn@evil.example/file", "https://o.thsi.cn:444/file"])
def testUntrustedDownloadRejected(tmp_path, url):
  """签名文件只能从已验证 HTTPS 主机下载。"""
  loader, calls = makeLoader(tmp_path, [sampleRow()], url)
  with pytest.raises(FuyaoError, match="地址"):
    loader.load("2026-09-15")
  assert len(calls) == 1


def testMissingValuesAreNotInvented(tmp_path):
  """无效成交额不转成零，目标日存在时仍可返回覆盖不足的映射。"""
  rows = [sampleRow(turnover=float("nan")), sampleRow(thscode="000001.SZ", turnover=-1.0)]
  loader, _ = makeLoader(tmp_path, rows)
  assert loader.load("2026-09-15")["turnovers"] == {}


@pytest.mark.parametrize("changes", [{"thscode": "../test"}, {"date_ms": DATE_MS + 1000}])
def testInvalidIdentityRejected(tmp_path, changes):
  """证券代码与日期时间都必须符合官方口径。"""
  loader, _ = makeLoader(tmp_path, [sampleRow(**changes)])
  with pytest.raises(FuyaoError):
    loader.load("2026-09-15")


def testDownloadLimitAndNetworkPrivacy(tmp_path, monkeypatch):
  """超限清理临时文件，网络异常不得回显签名或密钥。"""
  loader, _ = makeLoader(tmp_path, [sampleRow()])
  monkeypatch.setattr("quant_forge.market.dumpHistory.MAX_DOWNLOAD_BYTES", 1)
  with pytest.raises(FuyaoError, match="大小"):
    loader.load("2026-09-15")
  assert not list(tmp_path.glob("*.part"))
  def broken(request, timeout):
    raise OSError("secret signed-url")
  loader = DumpHistory(cacheDir=tmp_path, apiKey="secret", opener=broken)
  with pytest.raises(FuyaoError) as error:
    loader.load("2026-09-15")
  assert "secret" not in str(error.value)
  assert "signed-url" not in str(error.value)


def testInvalidDateAndMissingSchema(tmp_path):
  """非法输入与损坏 schema 明确拒绝。"""
  loader, calls = makeLoader(tmp_path, [sampleRow()])
  with pytest.raises(FuyaoError, match="日期"):
    loader.load("../2026-09-15")
  assert not calls
  loader, _ = makeLoader(tmp_path, [{"thscode": "600519.SH"}])
  with pytest.raises(FuyaoError, match="字段"):
    loader.load("2026-09-15")


def testLoadSavesEvidenceWithoutRepeatedDownload(tmp_path, monkeypatch):
  """验证完整 load 保存证据，使用本地已存在文件避免重复网络读取。"""
  artifact = tmp_path / "fixture.parquet"
  artifact.write_bytes(makeParquet([sampleRow()]))
  loader = DumpHistory(cacheDir=tmp_path)
  monkeypatch.setattr(loader, "fetchMetadata", lambda: ({"code": 0}, "unused"))
  monkeypatch.setattr(loader, "download", lambda _: (artifact, "test-hash", artifact.stat().st_size))
  result = loader.load("2026-09-15")
  assert artifact.with_suffix(".json").exists()
  assert result["rawResponses"][0]["matchedCodes"] == 1
