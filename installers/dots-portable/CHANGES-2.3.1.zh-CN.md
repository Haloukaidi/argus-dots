# 2.3.1：Reviewer测试同步修正

仅更新tests/test_dots_supervised.py及tests/test_dots_supervised_daemon_log_alias.py两个测试文件和对应payload。原typed审批仍真正执行；等待原transport.tool_result持久写入并释放锁后才发Event，再读取真实ready回复，避免fixture以1ms循环争抢同一journal锁。

原公开c367792的CI三项失败保留。受控0.7秒持锁可耗尽原0.5秒锁等待并触发取消，重现同一active-turn错误；原CI未采集完整现场，历史原因仍为推断。新旧fixture在同一延迟下红/绿复验；新增两个慢持久回复参数case，原候选变更、日志追加、别名重定向、停止及到期拒绝仍验证。

不改生产代码、原5秒测试预算、锁等待限制或审批/终态断言，不跳过或删除测试。新增完整2.3.0的87文件hash升级映射，保留所有旧map、731基准和8文件legacy；3个执行入口安装脚本字节不变。

精确验证与平台限制见CI-FIX-2.3.1.txt、CI-FIX-2.3.1.json及VALIDATION.json。原2.3.0的780/14源码门禁、609安装后源码测试与native验收为历史记录；本版没有重跑完整native或全库CI，发布后须验证新commit。
