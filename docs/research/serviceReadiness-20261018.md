# 首版新闻与 AI 服务检查

检查日期：2026-10-18，北京时间。
目的：核实本机已有服务能否支撑新需求，避免在已有可用配置时重复申请服务。

## 一、检查结论

已找到可复用的同花顺 Fuyao 凭据及 DeepSeek 官方 API 凭据，并完成六次有界真实接口检查。
建议首版采用同花顺快讯作为初始信息入口，DeepSeek 承担结构化研究及补充检索。
这是接入建议，不代表新系统已经实现，也不代表已验证所有资讯、行情和策略数据。

| 服务 | 本机配置状态 | 本轮真实验证 | 可承担的首版能力 |
| --- | --- | --- | --- |
| 同花顺 Fuyao | 进程和系统环境中存在 `HITHINK_FINANCE_API_KEY` | 快讯列表 HTTP 200、业务码 0，返回 20 条记录 | 初始新闻流；既有行情适配器继续复用 |
| DeepSeek | 现有 Claude Code 设置中配置了官方地址和凭据 | 账户资格、模型列表和一次模型调用均成功 | 有证据材料输入的在线 AI 研究 |
| DeepSeek Search | 使用同一 DeepSeek 凭据 | 搜索返回一条政府站点结果 | 按需补查政策、产业与公司关联证据 |
| DeepSeek Fetch | 使用同一 DeepSeek 凭据 | 成功抓取上述结果正文 | 为检索结果补充可归档的原文证据 |

本次只检查配置是否存在、服务主机和必要元数据；没有输出或保存密钥和账户余额金额。
也没有修改已有开发工具设置、系统环境变量或项目运行配置。

## 二、验证记录

### 1. 配置检查

- 现有应用源码只读取 `HITHINK_FINANCE_API_KEY`，尚未接入模型或新闻服务。
- 本机常见模型及新闻环境变量中，检查到上述同花顺变量；没有检查到 `DEEPSEEK_API_KEY`。
- 现有 Claude Code 设置中，`ANTHROPIC_BASE_URL` 指向 `api.deepseek.com`，凭据字段已填写。
- 客户端模型配置包含 `deepseek-v4-pro[1m]`；实际 API 模型编号以模型列表返回值为准。
- 检查不是对本机所有软件或账号的穷举，不能据此断言不存在其他可用服务。

### 2. 六次真实请求

| 请求 | 方法与接口 | 结果 | 证明范围 |
| --- | --- | --- | --- |
| 账户调用资格 | `GET https://api.deepseek.com/user/balance` | HTTP 200，`is_available=true` | 凭据有效，服务报告当前具备调用余额 |
| 模型列表 | `GET https://api.deepseek.com/models` | HTTP 200，含 `deepseek-flash`、`deepseek-v4-pro` | 本次凭据可见的模型编号 |
| 快讯首屏 | `GET https://fuyao.aicubes.cn/api/news/flash/list?tag_id=21103&seq=0` | HTTP 200、业务码 0，20 条记录 | 文档示例栏目本次可读；首条含标题、摘要和创建时间 |
| 模型生成 | `POST https://api.deepseek.com/chat/completions` | HTTP 200，返回合法 JSON，结束原因 `stop` | `deepseek-v4-pro` 的最小真实生成通过 |
| 原站搜索 | `POST https://api.deepseek.com/v1/tools/search` | HTTP 200，返回一条含标题和日期的结果 | 同一凭据的搜索接口本次可调用 |
| 原文抓取 | `POST https://api.deepseek.com/v1/tools/fetch` | HTTP 200，标题及正文非空 | 对本次搜索命中的政府网站页面抓取成功 |

模型验证完成于北京时间 23:37:08，输入 48 Token、输出 6 Token。
提示内容仅为生成连接确认 JSON，没有发送项目源码、账户信息或交易数据。
此次调用只验证接口与结构化输出能力，没有评估金融研究质量。

搜索限定 `site:gov.cn`，返回主机为 `chinawto.mofcom.gov.cn`；正文为 2,106 字符。
本次不将该搜索结果当作当前投资机会，也不将一个页面的成功推广为所有网站均可抓取。

请求均禁止自动跨站重定向。异常处理只输出状态或脱敏原因，不输出请求头和原始异常内容。

## 三、官方文档与接入边界

- [同花顺快讯](https://fuyao.aicubes.cn/docs/api-reference/news-flash/)：定义快讯列表、刷新及游标字段。
- [同花顺资讯详情](https://fuyao.aicubes.cn/docs/api-reference/news-article-detail/)：详情仅返回可对外提供的内容。
- [同花顺资讯事件库](https://fuyao.aicubes.cn/docs/api-reference/news-events/)：目前标记为暂未开放外部接入。
- [DeepSeek 模型列表](https://api-docs.deepseek.com/api/list-models/)：模型编号及能力以实际返回为准。
- [DeepSeek 账户资格](https://api-docs.deepseek.com/api/get-user-balance/)：本轮只记录是否可调用。
- [DeepSeek 结构化输出](https://api-docs.deepseek.com/api/create-chat-completion/)：JSON 输出仍需应用层校验。
- [DeepSeek 搜索与抓取](https://api-docs.deepseek.com/zh-cn/guides/search/)：搜索及抓取按服务规则计费和限流。

快讯列表可读与资讯事件库外部开放是不同能力，不能混为一谈。
旧仓库资料对新闻可用性的描述不能代替本次最新接口核验；本轮也不覆盖历史研究的其他限制。

## 四、仍需在实现阶段验证

1. 快讯栏目范围、翻页与刷新边界、更新及同源转载的去重语义。
2. 原始出处、原文引用及其与摘要之间的关系；缺少来源时不能伪装成已核实原文。
3. 宏观、行业和海外信息的覆盖及延迟，不能凭 20 条快讯声称全覆盖。
4. AI 产业链判断、公司业务关联、旧闻识别及事实支持率。
5. 大单、小单、主动买卖、分时、历史交易资格和板块成分等策略输入。
6. 每轮请求与 Token 上限、缓存、超时、限流、可用余额变化和失败降级。

独立系统应读取专用运行环境变量。建议采用 `DEEPSEEK_API_KEY`，
由本机运行配置完成设置；不能让业务服务长期依赖读取另一个开发工具的个人设置文件。
本次没有复制或迁移现有凭据。
