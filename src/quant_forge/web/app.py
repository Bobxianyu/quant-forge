"""提供同源页面、快照读取及受保护的手动刷新接口。"""

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

from quant_forge.market.marketService import MarketService
from quant_forge.market.snapshotStore import SnapshotStore

STATIC_DIR = Path(__file__).parent / "static"
ASSETS = {"app.js", "marketView.js", "styles.css"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def createApp(dataDir="outputs/market", collector=None):
  """创建本机应用；测试可注入采集器，不执行启动时自动采集。"""
  if collector is None:
    from quant_forge.market.collector import FuyaoCollector
    from quant_forge.market.dumpHistory import DumpHistory

    collector = FuyaoCollector(
      cacheDir=Path(dataDir) / "cache",
      historyLoader=DumpHistory(cacheDir=Path(dataDir) / "dumps"),
    )
  service = MarketService(SnapshotStore(dataDir), collector)
  token = secrets.token_urlsafe(32)

  @asynccontextmanager
  async def lifespan(app):
    yield
    service.close()

  app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
  app.state.marketService = service

  @app.exception_handler(HTTPException)
  async def handleHttpError(request: Request, error: HTTPException):
    """以中文返回未知路由及不支持的方法。"""
    message = {404: "资源不存在", 405: "不支持此请求方法"}.get(error.status_code, "请求无法处理")
    return JSONResponse({"error": {"message": message}}, status_code=error.status_code)

  @app.middleware("http")
  async def protectLocalAccess(request: Request, callNext):
    """限制主机及跨站请求，所有写入要求随机防伪令牌。"""
    host = request.headers.get("host", "")
    hostName = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if hostName not in LOCAL_HOSTS:
      return JSONResponse({"error": {"message": "仅允许本机访问"}}, status_code=400)
    if request.method not in {"GET", "HEAD"}:
      origin = request.headers.get("origin")
      supplied = request.headers.get("x-csrf-token", "")
      invalidOrigin = origin is not None and origin != f"{request.url.scheme}://{host}"
      if invalidOrigin or not secrets.compare_digest(supplied, token):
        return JSONResponse({"error": {"message": "请求来源或防伪令牌无效"}}, status_code=403)
    response = await callNext(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
      "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
    )
    return response

  @app.get("/api/session")
  def getSession():
    """返回仅同源页面可读取的防伪令牌。"""
    return {"token": token}

  @app.get("/api/market")
  def getMarket():
    """读取快照状态，不触发上游采集。"""
    return service.readMarket()

  @app.post("/api/market/refresh", status_code=202)
  def refreshMarket():
    """接受后台刷新，已有任务时返回冲突。"""
    if not service.startRefresh():
      return JSONResponse({"error": {"message": "已有采集任务正在运行"}}, status_code=409)
    return {"refreshing": True}

  @app.get("/")
  def getHome():
    """返回正式工作台入口。"""
    return FileResponse(STATIC_DIR / "index.html")

  @app.get("/{asset}")
  def getAsset(asset: str):
    """仅发布明确允许的前端资源，不挂载数据目录。"""
    if asset not in ASSETS:
      return JSONResponse({"error": {"message": "资源不存在"}}, status_code=404)
    return FileResponse(STATIC_DIR / asset)

  return app


def main():
  """启动单进程本地服务，默认端口 8765。"""
  import argparse

  import uvicorn

  parser = argparse.ArgumentParser(description="启动真实行情工作台")
  parser.add_argument("--port", type=int, default=8765, help="本地端口")
  parser.add_argument("--data-dir", default="outputs/market", help="行情快照存储目录")
  args = parser.parse_args()
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
  uvicorn.run(createApp(args.data_dir), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
  main()
