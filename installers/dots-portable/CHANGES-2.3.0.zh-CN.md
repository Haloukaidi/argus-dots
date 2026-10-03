# 2.3.0：显式监督近似模式

新增协议4及明确的supervised-approx-v1 profile，把原Web入口和daemon子进程绑定同一有限、在线、获授权的native host。原严格dots、九个CLI provider、角色提示、调度、Reviewer typed审批和原输出路径保留；dots不回退CLI。

近似profile逐项标记指令性限制，精确协商model/effort及显式effort映射；强隔离、未知参数、凭据等仍拒绝。短lease、scope及调用数限制不构成永久服务或平台权限。候选与证据快照在终态复核，daemon原日志别名按明确host来源验证；它们是事后检测，不是操作系统防写入。

费用与token未知保持unknown/null，空账本不等于零花费；新增原前端组件用量显示测试。预编译bundle未重建。

便携包增加精确2.2.0完整64文件升级映射，保留2.0.0、2.1.0、2.1.1、2.1.2及legacy映射。新增原件有hash预检和完整备份；用户后改拒绝，支持多代rollback及恢复首次原状态的uninstall。当前测试与历史测试分开记录，详见VALIDATION.json。

安装器另修复已复现的rollback/recover日志漏项：读取journal时先验证完整受管集合、已知版本hash及前后状态指纹；不完整记录在写文件前拒绝，原正常事务/恢复控制流程不变。
