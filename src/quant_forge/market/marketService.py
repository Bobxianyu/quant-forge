"""后台采集各行情模块，单模块失败保留此前快照。"""

import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from quant_forge.market.fuyaoClient import FuyaoError
from quant_forge.market.snapshotStore import MODULES

LOGGER = logging.getLogger(__name__)
COLLECTOR_METHODS = {"indices": "collectIndices", "sectors": "collectSectors", "stocks": "collectStocks"}


class MarketService:
  """协调单实例手动刷新；store 为快照仓库，collector 为采集适配器。"""

  def __init__(self, store, collector):
    self.store = store
    self.collector = collector
    self.lock = Lock()
    self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="market-refresh")
    self.future = None
    self.refreshing = False
    self.errors = {}

  def readMarket(self):
    """返回各模块最近成功结果；损坏记录局部报错，不伪造数据。"""
    modules = {}
    for module in MODULES:
      try:
        modules[module] = self.store.readModule(module)
        currentError = self.errors.get(module)
        if currentError:
          modules[module]["error"] = currentError
      except (OSError, ValueError, KeyError, TypeError):
        LOGGER.exception("读取 %s 行情快照失败", module)
        modules[module] = {"snapshot": None, "error": "本地快照读取失败，请检查文件后重新采集", "lastAttemptAt": None}
    return {"refreshing": self.refreshing, "modules": modules}

  def startRefresh(self):
    """启动一次后台刷新；已有任务返回 False。"""
    with self.lock:
      if self.refreshing:
        return False
      self.refreshing = True
      self.future = self.executor.submit(self.refreshModules)
      return True

  def refreshModules(self):
    """模块之间独立采集，单模块内部仍按供应商限流顺序请求。"""
    try:
      with ThreadPoolExecutor(max_workers=len(MODULES), thread_name_prefix="market-module") as executor:
        list(executor.map(self.refreshModule, MODULES))
    finally:
      with self.lock:
        self.refreshing = False

  def refreshModule(self, module):
    """保存单个模块，异常只更新其失败状态并保留上次完整快照。"""
    try:
      self.store.saveSnapshot(module, getattr(self.collector, COLLECTOR_METHODS[module])())
      self.errors.pop(module, None)
    except Exception as error:  # noqa: BLE001 -- 后台任务边界必须隔离未知异常。
      # 不将任意上游异常文本返回浏览器，避免带出凭据或内部路径。
      LOGGER.error("%s 采集或保存失败，异常类型：%s\n%s", module, type(error).__name__,
                   "".join(traceback.format_tb(error.__traceback__)))
      label = {"indices": "四项指数", "stocks": "个股行情", "sectors": "板块归属"}[module]
      message = f"{label}采集或保存失败，请检查网络、接口权限和本地存储；保留上次成功数据"
      if isinstance(error, FuyaoError):
        # 此类型只包含适配器生成的脱敏中文原因，不包含上游原始错误。
        message = f"{label}采集失败：{error}；保留上次成功数据"
      self.errors[module] = message
      try:
        self.store.recordFailure(module, message)
      except (OSError, ValueError, TypeError):
        LOGGER.exception("%s 失败状态写入失败", module)

  def waitForRefresh(self):
    """等待当前刷新结束，供正常关闭及测试使用。"""
    if self.future is not None:
      self.future.result()

  def close(self):
    """关闭时等待已开始的刷新，避免留下正在写入的文件。"""
    self.executor.shutdown(wait=True)
