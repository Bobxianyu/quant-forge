# 手动诊断工具

这些脚本用于已有快照和历史数据的人工核验，与日常单元测试分开。
脚本保持原有诊断口径，所有命令均须从仓库根目录执行。

先按项目 README 安装 Python 开发依赖，并通过 npm ci 安装浏览器验证依赖。

| 文件 | 用途 | 输入与输出 |
| --- | --- | --- |
| checkDump.py | 对账已经下载的历史成交额与逐股缓存 | 读取指定的历史 Parquet 和缓存，输出差异统计；不联网 |
| completeStockSnapshot.py | 复用已有缓存继续真实股票采集 | 访问真实接口，写入 outputs/market/ 并发布新的股票快照 |
| liveWorkbench.cjs | 使用已保存真实行情检查工作台 | 读取本地运行服务，输出统计及 outputs/live-market-workbench.png |

在仓库根目录按需执行：

```powershell
python tools/diagnostics/checkDump.py
python tools/diagnostics/completeStockSnapshot.py
node tools/diagnostics/liveWorkbench.cjs
```

checkDump.py 保留特定历史文件和交易日，适用于对应历史记录，不是通用下载入口。
completeStockSnapshot.py 需要后端环境中的 HITHINK_FINANCE_API_KEY，会消耗接口额度并写入数据。
liveWorkbench.cjs 需要工作台在 http://127.0.0.1:8765 运行、已有完整快照以及可用的 Edge 浏览器。
该脚本只读服务，不点击刷新，但会更新本地验收截图。

日常自动化验证使用 npm test 和 npm run test:browser；后者采用隔离响应，不采集真实行情。
本地第三方文档副本位于 outputs/reference/，手工报告位于 outputs/manual-report/，均不提交 Git。
