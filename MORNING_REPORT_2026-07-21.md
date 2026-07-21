# 晨间报告 — 2026-07-21 Shadow/Pilot 观察日

生成:09:40 PDT,盘中。系统全程 `READ_ONLY`、kill switch 常开、无下单工具、无真实交易。

## 一句话结论

**实验在跑,数据在收。** 06:35 起的 market gate 和全部 pilot 采样(07:03–09:23,共 12 个 run)全部 `COMPLETED`,20 分钟一个不落;唯一失败的是 06:10 canary(raw 快照采集),根因已在 09:32 修复并验证转绿。今天按计划就是 read-only Pilot 日,不产生正式业绩数据。

## 今天到目前的成绩单

| 时段 | Run | 状态 |
|---|---|---|
| 06:10 | CANARY (raw 快照) | ❌ FAILED_CLOSED(见下,已修复) |
| 06:35 | MARKET_GATE | ✅ agent 完成;六项检查 4 PASS / 1 FAIL / 1 UNKNOWN |
| 07:03–09:23 | 8× PILOT_SAMPLE | ✅ 全部 COMPLETED_NO_TRADE |
| 09:32 | 手动 CANARY(修复后) | ✅ COMPLETED,快照+SHA-256 验证通过 |
| 09:43–11:23 | 6× PILOT_SAMPLE | ⏳ 按计划自动执行 |
| 13:05 | CLOSE_SUMMARY | ⏳ 本地日志汇总 |

- Market gate 的 1 FAIL = "immutable raw MCP snapshot"(即 canary 同一根因),1 UNKNOWN = 依赖它的 replay equality。**今天六项官方检查未全过 → Formal Shadow 依旧未授权**,这与预期一致:迁移文档本就要求新 runtime 首日按全新 qualification 日对待。
- Pilot 采样质量:报价新鲜度全部 PASS(最老 5.9s < 10s 限);冻结 universe 纪律正确执行(GOOGL/SOFI/XOM 等不在 10 symbol 冻结名单内的 slot 未被擅自加入);目前无候选合约通过筛选,故无虚拟 policy trade,quote trajectory 为空——这是数据事实,不是故障。

## 夜里发生了什么(时间线)

1. **00:00–00:40** 修好三件环境事:仓库迁移后 launchd worker + watchdog 仍指旧路径(watchdog 已连续失败 648 次)→ 重新生成 plist 指向 `/Users/ge/ge/aitrading` 并重载;注册 `robinhood-trading` MCP(user scope);你完成了 OAuth。
2. **00:18–00:38** 三次 canary 失败,三种不同原因。根本矛盾:旧设计要求子 agent 把全部原始 MCP 响应逐字回吐进"最终消息",而 Claude Code 会把超限工具输出溢出到磁盘、挤出上下文——物理上做不到,agent 正确地拒绝编造。7-20 那三次"成功 canary"其实全是 Codex runtime 跑的,Claude runtime 从未成功过。
3. **00:40–01:00** 重写采集架构为 **stream-json 收割**:agent 只负责按清单调用有界的只读工具;本地确定性代码从事件流中逐字收割每个工具的请求/响应、强制完备性(六类工具缺一不可)、拒收任何非 JSON 结果(截断通知)、从响应内真实时间戳推导 `source_updated_at`、写入不可变 vault(vault 独立二次拒收 account_number/token 等键)。**模型从此碰不到数据本身,想错都错不了。** 293 项测试全绿。
4. **06:10** 定时 canary 仍失败——最后一个坑:`get_earnings_calendar` 没有 symbol 参数(实测其 schema 确认),返回全市场日历→溢出→解析器按设计 fail-closed。
5. **09:30–09:38** 换用 symbol 级的 `get_earnings_results`(实测 schema:单标的、尾部 8 季度、天然有界);09:32 canary **COMPLETED**(13 次调用含分页,快照 287KB,SHA-256 双重验证);又发现并修复 `source_updated_at` 会被期权到期日等未来时间戳污染的问题(现在钳制在采集时刻+5 分钟内,只认观察时间戳)。**295 项测试全绿。**

## 安全边界(未动摇)

- 全程只读:worker/collector 只暴露 `get_*` 工具;raw 采集进一步收窄为纯行情工具,**连账户类工具都不给**。
- Fail-closed 全保留:每次失败都留了原因、没有任何坏数据入库;错过的采样一律不回补。
- 解析边界仍是本地确定性代码,且比之前更强(数据不再经过模型转述)。
- kill switch 常开、live=false、order_tools=false,从未改变。

## 已知残留(不影响今天,需要你过目)

1. **09:32 那份快照的 `source_updated_at` 是 2026-07-28(未来)**——它产生于时钟钳制修复之前。vault 不可变,该快照本身是 PILOT_EXCLUDED 证据,建议保留并以本报告为注记。
2. 今天全部代码改动**未提交**(你没让我 commit)。改动清单:`execution/official_mcp_collector.py`(收割重写)、`prompts/robinhood_raw_collector.md`(新合同)、`tests/test_official_mcp_collector.py`(29→31 项)、`tests/test_generate_plist.py`(修一个机器相关断言)。建议你 review 后提交。
3. `config/` 下已入库的两份 plist 仍指旧路径(已加载的 `~/Library/LaunchAgents` 版本是对的);从 config/ 重载会重新弄坏调度,重载前必须用 `scripts/generate_shadow_worker_plist.py` 重新生成。
4. 旧目录 `/Users/ge/Documents/AI trading agent/...` 里还留着 7-20 的历史证据文件,历史 receipt 里的 `snapshot_path` 指向那里;是否迁移由你决定。
5. 明天(或下个观察日)需要:`python3 scripts/generate_shadow_worker_plist.py <日期>` 重装 + `python3 main.py scheduler-expect-day <日期>`(plist 单日 pin 是刻意设计,防止无人授权的自动续期)。
6. 无人值守 Bash 限制备忘:`shasum`、heredoc 重定向在 pilot 允许清单外,agent 已知改用 python3/Write。

## 明确没做的事

没有下过任何真实或模拟订单;没有动 kill switch、资金边界、策略参数;没有授权 Formal Shadow(那需要六项官方检查全过 + 你的显式授权)。
