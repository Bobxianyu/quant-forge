"""使用标准库访问同花顺接口，凭据只保留在请求头。"""

import json
import os
import time
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE_URL = "https://fuyao.aicubes.cn"
KEY_ENV = "HITHINK_FINANCE_API_KEY"
DEFAULT_TIMEOUT = 20
MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 2
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
SENSITIVE_FIELDS = {
  "x-api-key", "api_key", "apikey", "authorization", "token", "secret", "download_url", "url", "presigned_url",
}


class FuyaoError(RuntimeError):
  """接口、格式或完整性校验失败。"""


class NoRedirect(HTTPRedirectHandler):
  """禁止携带鉴权头跟随重定向。"""

  def redirect_request(self, req, fp, code, msg, headers, newurl):
    """重定向交给调用方作为请求失败处理。"""
    return


def readApiKey():
  """读取进程或 Windows 系统凭据，缺失时返回中文错误。"""
  value = os.environ.get(KEY_ENV, "").strip()
  if not value and os.name == "nt":
    import winreg
    try:
      with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                          r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as key:
        value = str(winreg.QueryValueEx(key, KEY_ENV)[0]).strip()
    except OSError:
      value = ""
  if not value:
    raise FuyaoError("同花顺客户端：未配置 HITHINK_FINANCE_API_KEY")
  return value


def redact(value, secret):
  """递归清除敏感字段和可能被上游回显的凭据。"""
  if isinstance(value, dict):
    return {key: "[已脱敏]" if key.lower() in SENSITIVE_FIELDS else redact(item, secret)
            for key, item in value.items()}
  if isinstance(value, list):
    return [redact(item, secret) for item in value]
  if isinstance(value, str):
    return value.replace(secret, "[已脱敏]")
  return value


class FuyaoClient:
  """有限重试的 REST 客户端，返回含时间及原始响应的脱敏证据。"""

  def __init__(self, apiKey=None, timeout=DEFAULT_TIMEOUT, opener=None, sleeper=time.sleep):
    """注入传输函数便于离线测试；默认拒绝跳转至其他地址。"""
    if not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 120:
      raise FuyaoError("同花顺客户端：超时必须在 0 至 120 秒之间")
    self.apiKey = apiKey
    self.timeout = timeout
    self.opener = opener or build_opener(NoRedirect()).open
    self.sleeper = sleeper

  def get(self, path, params=None):
    """请求固定站点 API，网络及限流重试，业务错误立即抛出。"""
    if not isinstance(path, str) or not path.startswith("/api/") or "?" in path or "#" in path:
      raise FuyaoError("同花顺客户端：接口路径必须以 /api/ 开头")
    params = params or {}
    if not self.apiKey:
      self.apiKey = readApiKey()
    request = Request(BASE_URL + path + "?" + urlencode(params), headers={"X-api-key": self.apiKey})
    for attempt in range(MAX_ATTEMPTS):
      try:
        with self.opener(request, timeout=self.timeout) as response:
          body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
          raise FuyaoError("同花顺客户端：响应超过大小上限")
        try:
          payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
          raise FuyaoError("同花顺客户端：响应不是有效 JSON") from None
        if not isinstance(payload, dict):
          raise FuyaoError("同花顺客户端：响应结构错误")
        code = payload.get("code")
        if code in {4001, 5001, 5002, 5003} and attempt < MAX_ATTEMPTS - 1:
          self.sleeper(RETRY_BASE_SECONDS * (2 ** attempt))
          continue
        if code != 0:
          safeCode = code if isinstance(code, int) else "未知"
          raise FuyaoError(f"同花顺客户端：接口业务码 {safeCode}")
        if not isinstance(payload.get("data"), dict):
          raise FuyaoError("同花顺客户端：响应缺少数据对象")
        return {"path": path, "params": redact(params, self.apiKey),
                "response": redact(payload, self.apiKey),
                "collectedAt": datetime.now(UTC).isoformat()}
      except HTTPError as error:
        if error.code not in {429, 500, 502, 503, 504} or attempt == MAX_ATTEMPTS - 1:
          raise FuyaoError(f"同花顺客户端：HTTP 请求失败，状态 {error.code}") from None
        retryAfter = error.headers.get("Retry-After") if error.headers else None
        if retryAfter and retryAfter.isdigit():
          self.sleeper(min(int(retryAfter), 120))
          continue
      except (URLError, TimeoutError, OSError):
        if attempt == MAX_ATTEMPTS - 1:
          raise FuyaoError("同花顺客户端：网络请求失败，已达到重试上限") from None
      self.sleeper(RETRY_BASE_SECONDS * (2 ** attempt))
    raise FuyaoError("同花顺客户端：请求未完成")
