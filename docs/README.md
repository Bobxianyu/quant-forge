# 文档导航

## 当前需求与设计

- [原始新需求](../新需求分析.md)。
- [需求迁移分析](research/requirementsMigration-20261018.md)：已确认范围、现有能力及待定规则。
- [首版设计草案](superpowers/specs/2026-10-18-decision-assistant-design.md)：待用户审阅，尚未开始实现。
- [新闻与 AI 服务检查](research/serviceReadiness-20261018.md)：现有服务的真实小范围验证。

## 当前可运行能力

- [项目启动与质量检查](../README.md)。
- [真实行情工作台](research/live-market-workbench.md)。
- [盘前规则与数据契约](superpowers/specs/2026-09-08-market-environment-design.md)。
- [新闻分析离线验证](research/news-pilot.md)。
- [手动诊断工具](../tools/diagnostics/README.md)。

## 行情接入与数据边界

- [同花顺接入梳理](research/fuyao-api-integration.md)与[真实接口联调](research/fuyao-live-verification.md)。
- [行情工作台验收](research/live-market-verification-20260916.md)与[字段覆盖核对](research/workbench-data-coverage.md)。
- [历史覆盖](research/fuyao-history-coverage-20260918.md)与[全市场历史核验](research/full-dump-audit-20260918.md)。
- [全年涨停与炸板池核验](research/year-pool-audit-20260926.md)。

## 历史研究

以下记录保存各自冻结范围、结果和限制，不代表新策略已经获得生产开仓资格。
研究计划与源码之间存在证据摘要绑定，整理目录时保留原路径和原文。

- [研究进度与结果索引](research/weight-task-progress.md)。
- [环境历史回放](research/environment-replay-20260918.md)。
- [首板观察研究](research/observation-study-result-20260927.md)。
- [封板研究](research/seal-hit-study-result-20260928.md)与[开放权重研究](research/open-weight-study-result-20260928.md)。
- [下行风险归因](research/weight-risk-diagnosis-result-20261003.md)。
- [盘中风险特征研究](research/intraday-risk-study-result-20261004.md)。
- [局部权重比较](research/local-risk-weights-result-20261008.md)。
- [跨期差异诊断计划](research/period-shift-diagnosis-plan-20261012.md)。

真实行情、下载文件、冻结面板和运行结果保存在本地 outputs/，不随源码推送。
因此历史报告中的本地证据路径在新克隆中可能尚不存在；不能据此伪造或覆盖原始材料。
