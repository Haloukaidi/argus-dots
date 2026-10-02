# portable 2.1.2：Reviewer文件定位符交接修复

相对2.1.1，原32项payload逐字节保留，新增2个原版生产文件的修复快照和3个测试，总计37项。安装器仍以原Argus `9cfe9129fd90511c3a1865844ec7dfda1b5d1008` 为固定基准；此次修复审阅基线为已发布2.1.1对应commit `aa619ebd6ff158c4bda4554c70a2ad96f251aef9`。

## 修复

原完整性标识清理会把文件名/目录中的长hex片段改写，造成Reviewer得到不存在的路径。仅在Reviewer专用入口保留明确且合法的现存文件定位符：Engineer summary中的文件、当前workdir固定source-cache目录、当前workdir或明确配置state-root内的host日志。

- 生产：`argus/core/model_visible_text.py`、`argus/roles/prompts/reviewer.py`
- 测试：`tests/core/test_reviewer_artifact_locators.py`、`tests/test_reviewer_artifact_handoff.py`、`tests/test_reviewer_host_locators.py`
- 原全局sanitizer默认、凭据清理、当前typed审批、角色权限和生产只读能力检查不变
- 不从路径文字推断新权限，不扫描/猜目录，不开放未授权roots；不存在/不可读/已删除文件仍必须阻塞
- background_context自由文本中的hex路径仍是已知未修范围，不能宣称所有路径已修复

## 验证与限制

相关37文件634项：620通过、14显式本地Docker probes跳过、0失败/错误；独审76项通过（重叠，不相加）。这是相关回归，不是新版本完整远端CI。

独立专用协调者的真实focused复测中，Reviewer实际读取32-hex目录内两个文件，原ReviewActions恰好1次审批dispatch；两个producer exit0、completed且consumed。本次正常完成交付通过。

取消干扰任务在审批ready前自然结束，因此该干扰INCONCLUSIVE。final准备到record完成的保守上界50.918秒，缺独立arrival戳，≤30秒目标未证明；原10-turn批次漏record导致的FAIL仍保留。没有增加timeout、重复审批或把本次成功扩为完整生产OS隔离/五角色验收。

操作改进仅写在外层手册：专用协调者优先record有效final并确认消费，报告/测试分析分离、最早deadline优先；权限、取消与过期语义不改。见 `HOST-PRIORITY.zh-CN.md`、`FOCUSED-HANDOFF-RESULTS.zh-CN.md`、`LOCATOR-VALIDATION.json`。

## 升级

受管版本2.1.2，精确兼容2.0.0旧20文件、2.1.0旧32文件、2.1.1旧32文件和原8文件legacy迁移。已有受管安装用 `--upgrade`。新修改的2个原版文件也备份原值；回滚恢复升级前版本，卸载恢复最初base或legacy。用户后改/不完整元数据仍拒绝覆盖。安装事务、bootstrap与native能力均不因版本号改变。
