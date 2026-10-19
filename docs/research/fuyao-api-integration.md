# 同花顺 Fuyao 数据接口接入准备

> 2026-09-15 更新：已使用系统环境变量完成小范围接口联调，四项核心指数代码及行情可读取。
> 本文保留准备阶段记录；当前实测状态见 [真实接口联调结果](fuyao-live-verification.md)。

核对日期：2026-09-14。
状态：文档与项目契约梳理完成；尚未实现在线采集，尚未使用 API Key 联调。

## 1. 阅读范围与依据

已获取站点全文聚合，重点阅读通用约定、股票行情、交易日历、指数、涨跌停、集合竞价和批量导出。
基金、财务、期货、期权等能力已了解目录，本阶段不作为盘前规则的主要输入。

- [API 总览](https://fuyao.aicubes.cn/docs/api-reference/overview/)
- [官方文档全文聚合](https://fuyao.aicubes.cn/llms-full.txt)
- [快速开始](https://fuyao.aicubes.cn/docs/quickstart/)
- [股票行情](https://fuyao.aicubes.cn/docs/api-reference/prices/)
- [交易日历](https://fuyao.aicubes.cn/docs/api-reference/calendar/)
- [指数与板块](https://fuyao.aicubes.cn/docs/api-reference/a-share-index/)
- [涨跌停与炸板](https://fuyao.aicubes.cn/docs/api-reference/limit-up-data/)
- [集合竞价](https://fuyao.aicubes.cn/docs/api-reference/auction/)
- [全市场导出](https://fuyao.aicubes.cn/docs/api-reference/market-dumps/)
- [开发工具与能力边界](https://fuyao.aicubes.cn/docs/developer-tools/overview/)

聚合文档与介绍页存在不同步之处：介绍页提到日、周、月 K，股票和指数端点参数表目前仅接受 `1d`。
实现时按具体端点契约处理，联调时保存实际响应；不能把介绍性文字或示例当作权限、覆盖率保证。

## 2. 接入协议与配置

建议首期由现有 Python 程序直接调用 REST，保留 CSV 作为规则引擎的稳定输入和离线回放边界。
MCP 适合交互查询，官方 Python SDK 可作为后续选择，本次未安装 SDK 或新增依赖。

| 项目 | 已核实约定或拟定方案 |
| --- | --- |
| 服务地址 | `https://fuyao.aicubes.cn` |
| 鉴权 | 请求头 `X-api-key`；登录站点后在 API Key 管理页创建凭据 |
| 凭据配置 | 拟读取 `HITHINK_FINANCE_API_KEY`，与开发工具文档一致；当前项目尚未读取此变量 |
| 标的代码 | 使用带市场后缀的 `thscode`，如 `600519.SH`；股票和指数必须区分资产类别 |
| 时间 | Unix 毫秒转换为带时区时间，交易日期统一按 `Asia/Shanghai` 解释 |
| 百分比 | `1.74` 表示 `1.74%`，映射项目 `Pct` 字段时不再乘除 100 |
| 返回体 | 检查 HTTP 状态及 `code`，成功必须为 `code=0`；再检查 `data` 和端点结构 |
| 追踪 | 保存端点、脱敏参数、`request_id`、业务码、采集时间和数据日期 |

凭据由本机用户环境或密钥服务注入，不写入源码、CSV、日志、命令参数或版本库。
此变量名称是客户端约定，不是服务器要求；服务器实际识别的是 `X-api-key`。

错误处理拟分为三类：

- 参数、认证、权限和标的错误：`1001`～`1004`、`2001`、`2003`、`3001`、`3004`，停止并报中文错误。
- 数据未就绪：`3002`，记录缺失状态，不转换为零，不沿用旧日期数据冒充本日结果。
- 限流和服务暂时失败：HTTP 429、`4001`、`5001`～`5003`，有限次数退避重试。

建议初始客户端策略为单并发、单次超时 15 秒、最多重试 2 次，使用指数退避与随机抖动；
若服务器提供 `Retry-After` 则尊重该值。这些是待实现的项目配置，不是供应商承诺。
总耗时受盘前截止时间约束；HTTP 非成功、非 JSON、空响应及未知业务码同样必须显式处理。
文档没有承诺固定 QPS；“不限制累计次数”不能解释为不受限流或无需权限。

## 3. 首期接口清单

以下均为 GET；表中参数仅列接入关键点，完整字段以对应官方页面为准。

| 用途 | 路径 | 参数和限制 |
| --- | --- | --- |
| 标的消歧 | `/api/meta/tickers/search` | `q`；使用 `asset_type` 区分股票和指数 |
| 代码表 | `/api/meta/tickers/list` | `asset_type`、`limit`、`offset` |
| 交易日期 | `/api/a-share/calendar/trading-days` | 无参数，固定近一年至今日，不能查询未来日历 |
| 股票快照 | `/api/a-share/prices/snapshot` | 批量 `thscodes`；省略时用 `limit/offset` 全市场分页 |
| 股票日 K | `/api/a-share/prices/historical` | 单个 `thscode`、`interval=1d`、毫秒 `start/end`；最长十年 |
| 板块目录 | `/api/a-share-index/catalog/ths-index-list` | `tag=cn_concept/industry/region/tszs`；无分页 |
| 当前成分股 | `/api/a-share-index/constituents/ths-stock-list` | 单个指数 `thscode`；无历史日期参数 |
| 指数快照 | `/api/a-share-index/prices/snapshot` | 必填批量 `thscodes`；不支持空参数枚举全指数 |
| 指数日 K | `/api/a-share-index/prices/historical` | 单个 `thscode`、`interval=1d`、`start/end`；无 `adjust` |
| 涨停池 | `/api/a-share/special-data/limit-up-pool` | 显式 `date_ms`、从 1 开始的 `page`、`size=1..200` |
| 跌停池 | `/api/a-share/special-data/limit-down-pool` | 同样明确日期并取尽分页，用于后续情绪扩展 |
| 炸板池 | `/api/a-share/special-data/limit-break-pool` | 同样明确日期并取尽分页，用于后续情绪扩展 |
| 连板天梯 | `/api/a-share/special-data/limit-up-ladder` | 无参数，近 30 交易日，每个档位最多 4 只 |

股票历史日 K 默认 `adjust=forward`，采集时必须显式选择并记录复权方式。
全市场历史数据量大时，可评估 API Key 入口 `/api/dump/market-dumps/...` 的 Parquet 导出。
浏览器 `/dump/...` 使用 Cookie，不能与 API Key 入口混用。
下载链接短时有效，不作为长期数据地址，不记录签名查询串；首期不自动下载十年全量文件。

## 4. 与现有项目的对接位置

当前边界已核对：

- `src/quant_forge/adapters/csv_adapter.py` 的 `load_market_premium` 读取四项必填溢价。
- 同文件 `load_sectors`、`load_external_markets`、`load_news_events` 读取另外三类输入。
- `src/quant_forge/domain/models.py` 定义四类领域快照。
- `src/quant_forge/pipeline.py` 的 `build_pre_market_decision` 负责日期、截止时间和规则编排。
- `docs/superpowers/specs/2026-09-08-market-environment-design.md` 定义 08:50 边界和业务含义。

拟定数据流：REST 采集 → 原始响应快照 → 字段校验与指标计算 → 标准 CSV → 现有规则引擎。
原始快照记录实际 `collectedAt`、请求日期、上游时间、参数和内容摘要，以支持溯源及重算。
在线采集器后续可放在 adapters 下，计算逻辑独立组织；本次不新增未验证的业务函数。

| 项目输入 | 可利用的数据 | 接入判断 |
| --- | --- | --- |
| `firstBoardPremiumPct` | 指数目录、指数日 K，或历史涨停池加次日行情 | 未找到该指标的直接同名字段；需核实官方指数及算法 |
| `secondBoardPremiumPct` | 涨停池 `is_st`、连板计数和次日行情 | 需核实非 ST 二板样本及收益口径 |
| `multiBoardPremiumPct` | 历史涨停池、日 K | “最近多板”时间窗口和样本规则尚未锁定 |
| `limitUpPremiumPct` | 历史涨停池、日 K | 昨日涨停样本的次日表现，不是当日涨停股自身涨幅 |
| 板块代码、名称 | 目录 `thscode/name` | 用真实代码替代示例 `AI/CHIP` 时需统一新闻板块映射 |
| `returnPct` | 指数行情涨幅或历史收盘价 | 指定交易日，统一复权及收益计算口径 |
| `breadthPct` | 成分股列表及其行情 | 计算上涨占比；需规定停牌、缺失、新股的分母处理 |
| `limitUpCount` | 板块成分股与同日完整涨停池 | 按 `thscode` 去重后取交集 |
| `turnoverChangePct` | 板块连续交易日成交额 | 拟计算成交额环比，前值为零或缺失时不可填零 |
| `relativeStrengthPct` | 板块收益与全市场股票收益 | 原规格要求全市场等权基准，不默认用沪深 300 替代 |
| `persistenceDays` | 多日板块指标 | 需先定义“强势”的判定条件，再连续计数 |
| `catalystScore/crowdingRiskScore` | 新闻分析及多日行情辅助 | 无已确认的一对一直取字段，需独立定义评分规则 |
| 外围行情 | 另行核对海外市场覆盖 | 跨市场代码检索不等于能取得美股或 A50 行情 |
| 结构化新闻 | 既有新闻实验及后续独立来源 | 异动原因和基金资讯不能直接满足完整风险事件契约 |

## 5. 最关键的时间与样本口径

令 T 为报告交易日，D 为 T 的上一交易日，P 为 D 的上一交易日。
08:50 的正式报告使用在 D 完整形成的四项指标，不能使用 T 当天竞价或盘中更新。

若后续需要自行计算“昨日涨停表现”，候选方法是用 P 的完整涨停池确定样本，
再观察这些股票在 D 的收益。首板、二板、多板也应先锁定 P 的分组，不能按 D 的幸存涨停股反推。
该方法只是候选定义，尚不能宣称等价于同花顺特色指数；需核对加权方式、ST/新股过滤、
连板计数与“几天几板”的区别、停牌缺失处理、除权除息以及“最近多板”的窗口。

优先通过指数目录和标的搜索核实是否存在匹配四项指标的指数及历史序列。
名称匹配只能作为候选，还需官方编制口径或样本对账。不得猜测指数代码后直接接入正式规则。

其他必须保留的边界：

- 连板天梯是截断展示，不能替代完整涨停池计算均值、样本数或涨停贡献。
- `auction/short-term-benchmark` 返回个股竞价涨幅与标签，不是四项收盘溢价汇总。
- `data.timestamp` 含义因接口而异：行情为上游有效时间，竞价接口为响应组装时间。
  实际采集时间单独记录，不能把历史 `date_ms` 写成采集时间以绕过截止校验。
- 批量行情的整体时间取最新上游有效时间，不能据此断言每只股票都已更新。
- 当前成分股不能直接回填多年历史，否则会引入成分变化和幸存者偏差；应逐日存档。
- 事后补采的历史数据可用于研究回放，但不能冒充当时已取得的正式盘前输入。
- 核心字段缺失继续触发 `INSUFFICIENT_DATA` 与零仓位，不得以零收益填补缺失值。

## 6. 接入前需要准备

1. 在 [API Key 管理页](https://fuyao.aicubes.cn/admin) 创建专用 Key，并安全配置到本机。
2. 验证账号是否有行情、历史日 K、日历、指数目录、成分股、涨停池的访问权限。
3. 用少量真实响应确认历史涨停池可查询范围、收盘数据更新时间和空数据语义。
4. 核实四项溢价的官方指数或冻结计算口径；确认板块分类与新闻事件代码对应关系。
5. 为美股、A50 和结构化新闻补齐单独的数据来源与权限。

本次未索取、读取或保存真实 Key；权限和实际响应完整性仍待联调验证。

## 7. 后续实现与验收顺序

1. 先写客户端测试：成功、缺 Key、HTTP 200 业务失败、权限不足、429、超时及非 JSON。
2. 实现最小 REST 客户端、参数校验、中文错误、脱敏记录及有限退避。
3. 先联调单股票行情和日历，再联调单板块及指定历史日期涨停池，最后取尽分页。
4. 先写归一化测试：百分比、毫秒时区、跨周末节假日、空池、重复页、漏股票、部分响应及停牌。
5. 在指标口径确定后，先写样本计算测试，再生成隔离目录内的标准 CSV 草稿。
6. 验证 D/P 对齐、截止后数据被拒绝、缺核心指标安全降级、相同输入可重复回放。
7. 运行项目全部测试和静态检查，核心覆盖率至少 80%；对账完成后再连接正式数据流程。

本次仅新增接入准备文档，没有修改运行代码、依赖、现有配置或正式报告，没有进行数据接口实测。
