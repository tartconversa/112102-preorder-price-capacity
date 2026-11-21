"""短途观光报价试营分析应用。

仅依赖 Python 标准库。模块划分：

- loading:  合成资料导入、结构校验、文件指纹
- identity: 时点去重（仅使用分组当时可见的合并证据）
- demand:   四类状态划分、部分识别需求区间、变价影响、共同支持
- finance:  收款/退款回执链、现金敞口、对账异常
- rights:   预约权利时间线、替代邀约与同意规则
- capacity: 可兑现席位与新放量方案
- reports:  Markdown / CSV 报告
- ledger:   决策版本台账
- cli:      命令行入口
"""

SCHEMA_VERSION = "1.0"
