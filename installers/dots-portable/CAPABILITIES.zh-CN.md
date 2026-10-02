# 能力与验收矩阵（portable 2.1.0）

表中的真实 native 协调、续接及 dummy typed-action 证据来自 2.0.0 阶段；本轮新增工作流 QA 使用受控后端，不把它称作重新完成的原生生产验收。

| 目标 | 当前实现 / 证据 | 仍有限制 |
|---|---|---|
| 原 Argus 五角色接入 | 显式 factory；原职责、capsule、反馈和 dispatcher 保留；相关回归通过 | 没有完整五角色原生生产链路实测 |
| 专用原生协调 worker | 有限显式任务清单；真实 spawn/bind/结果/中断验收 | 必须有在线、已授权宿主；不扫描任意新文件 |
| 去重、并发、退出恢复 | 先落意图、真实 worker 映射、generation 接管、摘要校验、原结果恢复 | 未知执行不会自动重跑，需实际证据核对 |
| 同角色稳定续轮 | 真实一次 spawn + 一次 followup，同 worker 记忆复述通过 | 同角色/mission/parent，单活跃调用；列表缺失不是未执行证明 |
| Typed 审查动作通道 | 原生 worker 实际 CLI 调用；schema 错误反馈后修正；原 dispatcher 两次观察 | 普通 final 文本/JSON不能批准；这不是 read-only Reviewer 验收 |
| 取消确认 | 真实 running→interrupted；有界 ack；阻止晚到输出 | native stop不代表任意OS进程/已启动callback已停；无settlement会保持阻塞 |
| 生产 Reviewer OS只读/工具限定 | 不声明；仍保留原能力检查并 fail-closed | 当前原生平台未提供所需可验证硬限制 |
| 默认后端与原 CLI | 默认provider不变；保留原9个CLI模型语义，补齐dots非CLI默认模型与空字符串规范 | 不静默换provider，也不注册不存在的dots CLI后端 |
| 实际源码修复 | 本版32文件payload：8个原版文件修改、24新增；含Git/路径/状态、Reviewer context及工具桥取消修复 | 固定基准与局部授权范围；源码修复没有增加native平台权限 |
| 便携升级与恢复 | 受管2.0.0完整20文件hash、实际文件、原始备份与元数据核验；显式--upgrade；真实升级/回滚/卸载通过 | 用户后改或未知/不完整安装拒绝，不提供force覆盖 |
| 用量、费用、配额 | 未知值保持unknown，presence=false | 未接入真实provider usage/费用预占/持久I/O账目 |
| 自动长期接收服务 | 没有 | 未安装常驻服务、外部agent或公网监听；有限会话最长3600秒 |
| 文件安全 | POSIX私有目录、no-follow、原子发布、锁与完整性审计 | 不防同UID恶意重写全部文件；未做其他平台验收 |

当前测试口径：全库Python无提前停止，12086 passed / 68 failed / 136 skipped / 0 errors，共12290项，857.84秒。剩余失败为59项procfs环境要求、8项AF_UNIX被拒、1项已有venv导致的原测试前提。最后一项原测试在无venv干净树独立复跑1/1通过，仍保留原全库失败计数。

npm check的构建、类型检查和226项测试通过。Python类型检查仍有与原基准完全相同的30项诊断，未宣称全绿。277项相关检查属于全库子集，不能重复相加。安装器另有44项标准库测试和61项独审通过，含真实固定基准升级/恢复循环。

精确环境、跳过原因和证据边界见 `SOURCE_VALIDATION.json` 与 `VALIDATION.json`。真实native证据与fixture测试分别列示，不能互相替代。
