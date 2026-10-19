"""验证真实客户端的鉴权、重试与脱敏。"""

import io
import json
from urllib.error import HTTPError, URLError

import pytest

from quant_forge.market.fuyaoClient import FuyaoClient, FuyaoError, readApiKey


def testAuthAndEvidence():
  """鉴权仅写请求头，不进入采集证据。"""
  requests = []
  def opener(request, timeout):
    requests.append((request, timeout))
    return io.BytesIO(json.dumps({"code": 0, "data": {"item": []}}).encode())
  client = FuyaoClient(apiKey="secret", opener=opener)
  result = client.get("/api/test", {"limit": 2})
  assert requests[0][0].get_header("X-api-key") == "secret"
  assert result["response"]["code"] == 0
  assert "secret" not in json.dumps(result)


def testRetryAndBusinessFailure():
  """网络错误有限重试，业务异常不会泄漏上游消息。"""
  calls = []
  def opener(request, timeout):
    calls.append(request)
    raise URLError("secret")
  with pytest.raises(FuyaoError, match="网络") as error:
    FuyaoClient(apiKey="secret", opener=opener, sleeper=lambda _: None).get("/api/test")
  assert len(calls) == 3
  assert "secret" not in str(error.value)
  def business(request, timeout):
    return io.BytesIO(b'{"code":2001,"message":"secret"}')
  with pytest.raises(FuyaoError, match="业务码 2001"):
    FuyaoClient(apiKey="secret", opener=business).get("/api/test")


def testRejectForeignUrlAndInvalidJson():
  """禁止将凭据发送到外部地址。"""
  client = FuyaoClient(apiKey="secret", opener=lambda *args, **kwargs: io.BytesIO(b'bad'))
  with pytest.raises(FuyaoError, match="路径"):
    client.get("https://example.com")
  with pytest.raises(FuyaoError, match="JSON"):
    client.get("/api/test")


def testRateLimitRetryAndRedaction():
  """限流退避后成功，原始响应中的鉴权和签名链接被清除。"""
  replies = [{"code": 4001}, {"code": 0, "data": {
    "item": [], "token": "secret", "url": "signed", "presigned_url": "temporary-signed-url",
  }}]
  waits = []
  def opener(request, timeout):
    return io.BytesIO(json.dumps(replies.pop(0)).encode())
  result = FuyaoClient(apiKey="secret", opener=opener, sleeper=waits.append).get("/api/test")
  assert waits == [2]
  assert result["response"]["data"]["token"] == "[已脱敏]"
  assert result["response"]["data"]["url"] == "[已脱敏]"
  assert result["response"]["data"]["presigned_url"] == "[已脱敏]"


@pytest.mark.parametrize("payload", [[], {"code": 0}, {"code": "secret"}])
def testMalformedEnvelope(payload):
  """异常信封不会穿透为成功响应。"""
  def opener(request, timeout):
    return io.BytesIO(json.dumps(payload).encode())
  with pytest.raises(FuyaoError):
    FuyaoClient(apiKey="secret", opener=opener).get("/api/test")


def testEnvironmentKeyAndTimeout(monkeypatch):
  """进程凭据可读取，非法超时立即拒绝。"""
  monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "secret")
  assert FuyaoClient().apiKey is None
  with pytest.raises(FuyaoError, match="超时"):
    FuyaoClient(timeout=0)


def testMissingKeyOnlyFailsOnRequest(monkeypatch):
  """缺少凭据仍能构造服务读取旧快照，真实请求时才报告错误。"""
  def missingKey():
    raise FuyaoError("未配置凭据")
  monkeypatch.setattr("quant_forge.market.fuyaoClient.readApiKey", missingKey)
  client = FuyaoClient()
  with pytest.raises(FuyaoError, match="未配置"):
    client.get("/api/test")


def testConfiguredProcessKey(monkeypatch):
  """优先使用进程内配置，不读取或输出系统密钥。"""
  monkeypatch.setenv("HITHINK_FINANCE_API_KEY", " test-only-key ")
  assert readApiKey() == "test-only-key"


@pytest.mark.parametrize("status", [401, 403, 302])
def testHttpPermissionAndRedirectFailWithoutRetry(status):
  """权限失败与重定向立即拒绝，错误不包含请求凭据。"""
  calls = []

  def opener(request, timeout):
    calls.append(request)
    raise HTTPError(request.full_url, status, "secret", {}, None)

  with pytest.raises(FuyaoError, match=str(status)) as error:
    FuyaoClient(apiKey="secret", opener=opener).get("/api/test")
  assert len(calls) == 1
  assert "secret" not in str(error.value)


def testRetryAfterAndTransientBusinessError():
  """尊重限流等待时间，临时业务错误随后允许恢复。"""
  replies = [HTTPError("https://fuyao.aicubes.cn/api/test", 429, "限流", {"Retry-After": "3"}, None),
             {"code": 5001}, {"code": 0, "data": {"item": []}}]
  waits = []

  def opener(request, timeout):
    reply = replies.pop(0)
    if isinstance(reply, HTTPError):
      raise reply
    return io.BytesIO(json.dumps(reply).encode())

  result = FuyaoClient(apiKey="secret", opener=opener, sleeper=waits.append).get("/api/test")
  assert result["response"]["code"] == 0
  assert waits == [3, 4]
