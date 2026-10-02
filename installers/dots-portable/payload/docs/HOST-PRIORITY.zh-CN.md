# Native host 完成确认与停止优先级（操作手册增补）

适用：有限、已授权 exact request batch 的 native coordinator。它不添加 Argus 决策角色，不改变原 Reviewer 审批语义，不提供 OS 沙箱。

## 职责分离

- 专用 coordinator 只承担 journal/native dispatch、bind、完成确认、stop/interrupt及真实状态核对
- 测试、源码分析、artifact加工、报告和打包由主持任务完成；不要让同一个协调任务分析报告后才记录已有 final
- 主持任务负责 producer 进程及截止时间记录，传入精确任务范围/输入/请求ID；coordinator不发现或自动执行目录中的新任务

## 每次唤醒先完成交付

1. 先核对该请求的取消和期限边界。已取消或过期仍拒绝晚到success，不能因为artifact存在或审批done而重开
2. 有真实native当前轮final且请求仍有效：下一可用执行机会立即record，携带该轮准确marker、worker身份和真实结果；不要先发进度、做报告、启动其它任务或跑测试
3. 记录成功后确认producer消费/退出结果，然后再汇报成功。record本身与上层已消费不是同一个事实
4. 对另一个请求的stop，实际interrupt后观察已停止，再写cancelled。不能把stop文件、发出interrupt、进程列表缺失当成停止证明
5. 同批多个等待事项按最早deadline处理；无待交付final或紧急取消才等待或admit新任务。不会因为等审批或等终态而无限延长批次

## 证据分层

- 完成marker：本轮结果相关性，不是artifact发布或审批
- 文件交付：宿主落盘+下游自己实际读取，记录cwd/路径/字节数；hash由host验证，不作为模型判断依据
- Typed审批：原ReviewActions当前请求调用及真实ready回复；不能由文字approve、测试成功或普通final制造
- 最终交付：当前final已record且producer确实消费；callback done不自动代表transport done

## 短final与独立审计

- Worker把详细读取观测写在该任务明确批准的audit路径，final只引用收据、当前request和实际结论，避免协调员手工转录长文延误
- 任何audit写权限仅对该受控fixture有效，不称为生产Reviewer写权限或OS隔离能力
- 记录工具实际时刻：native最后一次准备final时刻、record工具开始/完成、terminal持久化、producer消费。若没有独立native到达戳，明确测的是上界或工具操作延迟，不捏造毫秒级到达时间
- 有效final收到后到record以≤30秒为目标；保留工具实际延迟和失败，不只通过加timeout让症状消失

## 失败和恢复

- 已有action reply但final缺失：先检查原journal和native真实结果，按原recover_result/reconcile恢复；不得自动重新执行typed action
- 未知spawn/followup结果：保持不确定并核对，不能盲目重发
- 批次容量失败、race未命中或超时分别记录BLOCKED/INCONCLUSIVE/FAIL；保留原历史，不无限追加worker凑PASS
- 本操作规则不删除sandbox options，不替代生产安全gate，不绕过原路径/权限边界

## 当前证据边界

本次focused实际读取、原typed审批和两个producer最终消费通过；取消干扰因另一任务先自然完成而INCONCLUSIVE。缺少独立final到达时间戳，final准备到record完成的保守上界为50.918秒，不能证明≤30秒目标；本规则不是自动实时保证。原较长批次审批完成后未及时record导致的超时仍保留为FAIL。没有扩大权限、变更任何timeout或取消/过期语义。
