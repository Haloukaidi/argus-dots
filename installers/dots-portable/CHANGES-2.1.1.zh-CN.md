# portable 2.1.1：纯测试回归修正

唯一变化的payload是 `tests/test_workflow_regression_triage.py`。生产代码、权限/认证/并发上限、取消处理、安装事务与bootstrap均未修改。

原状态测试的http.client helper分开写请求头和body，在服务端饱和后提前关闭连接时，body写入可能先抛BrokenPipeError。新的helper一次sendall完整小请求后解析真实HTTP响应；这不保证TCP原子性。保留严格503断言、取消成功、清理同步和最终6个200结果，不增加sleep、重试、跳过或伪造状态。

新增4项正式client异常映射与1项真实早关连接测试；旧断言没有减少。原回归及真实早关连接各100/100通过，独评全文件26项和7项额外安全输入通过。较广范围511通过、18跳过、1项AF_UNIX环境拒绝（原版同样复现）。这些结果与历史全库结果重叠，不能相加，也不能说新commit完整CI已通过。

测试属于受管源码payload，因此显式升级模块版本为2.1.1，避免同版本不同内容。manifest保留旧2.0.0完整20文件映射，加入旧2.1.0完整32文件映射，保留原727基准及8文件legacy。现有受管安装用 `--upgrade`；`--rollback`恢复升级前的确切版本，`--uninstall`恢复首次安装之前状态。用户后改或备份损坏仍拒绝覆盖。

详细因果与证据：`CI-FIX-REPORT.md`、`FIXTURE_VALIDATION.json`。仓库继续使用稳定路径 `installers/dots-portable/`；ZIP目录名称才包含2.1.1版本号。
