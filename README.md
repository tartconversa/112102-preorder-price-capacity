# 带订金预约的价格试探与兑现边界

观光服务报价试营、预约条款和银行回执的合成资料，以及一套仅依赖 Python 标准库
（Python 3.14）的本地分析应用：估计各价格条件下可确证的需求范围、按截止日重放
退款与改期事件、计算可兑现席位与新放量方案，并把每一版放量决策连同输入指纹存档。

所有标识、金额、地名和观察值均为合成数据。

## 来源资料（`sources/`）

- `offers.csv`：按市场及试营业区段（block）封存的完整报价分配表。`price` 和
  `deposit` 为整单金额；`deposit=0` 表示只提供意向表；`sold_out` 是尝试中遇到
  暂停销售，且不改变原分组。
- `commerce_events.csv`：每行是单独收到的回执。同一订单的退款发出与退款撤回
  各用独立行；`replaces` 引用被撤回的回执。
- `identity_links.csv`：账号合并证据，只在 `learned_day` 起对经营人员可见。
- `sales_terms.csv`：财务签发的收款条款（保原价、保同行同场、替代须同意）。
- `sessions.csv`：按 session 和 `learned_day` 保留各次状态（含取消）。
- `booking_rights.csv`：同一权利的往次状态；状态属于预约系统，到账和冲回另由
  银行回执记录；缺少 `expires_day` 的基础权利没有替代邀约到期时间。

## 快速开始

```sh
# 全量测试（含原始来源契约测试，共 41 个；-I 隔离环境变量与 cwd 注入）
python -I run_tests.py -v
# 也可用标准方式：python -P -m unittest discover -s tests -v

# 按截止日重出全套分析（报告与 CSV 写入 data/reports/）
python -I run.py analyze baseline --as-of-day 14

# 提交一版放量决策（额外写入 data/decisions/ 台账）
python -I run.py plan baseline --as-of-day 14 --market east --note "首批放量"

# 导入新一轮合成资料（只复制约定的六张 CSV，先校验后原子落位）
python -I run.py import-round /path/to/new/drop --name round2
python -I run.py analyze round2

# 决策追溯
python -I run.py decisions
python -I run.py decision <版本号>
```

`python -I` 启用隔离模式（忽略环境变量与用户 site-packages）；`run.py` 会按自身
位置把项目根加入 `sys.path`，因此从任意 cwd 调用都安全。处理外部资料目录时，
不要 `cd` 进该目录再启动解释器，避免其中的同名 `.py` 被导入。

## 经营约束如何落实

### 1. 四类家庭状态与锁定分母

每份报价按截止日判定为：`converted`（有效付款转化）、`self_abandon`（自行放弃）、
`no_opportunity`（系统未提供机会，sold_out）、`intent_only`（仅登记意愿），
另有 `refunded`（曾付款但退款当前生效，属观测到的硬拒绝）。

分母只来自封存的 offers 表，按 block 分配日可见的身份关系计算家庭组件；
候补转正、权利变更、迟到回执都**不能重新定义分母**，异常订单也只冻结容量、
不从分母删除。

### 2. 时点身份（`app/identity.py`）

逐 block 在其 `assigned_day` 用当日已可见的 identity link 做并查集。基线数据中
J1（第 11 日知晓，连接 150 价与 90 价账号）、J2（第 12 日，连接 120 与 150 价
账号）都晚于所在 block 的分配日，因此从不参与归并；报告的「时点身份审计」
逐链接说明若强行归并会跨越哪个价格单元。

### 3. 需求的部分识别（`app/demand.py`）

不做点估计，给出资料可确定的需求区间（Manski 界）：

- 下界 L = 有效付款家庭数 / 家庭分母 n
- 上界 U = (n − 硬拒绝家庭数) / n，硬拒绝 = 自行放弃 + 退款生效
- sold_out（系统未给机会）与 intent_only（仅登记）处于未知带，只进上界

变价影响用区间差表达：Δ(p_hi−p_lo) ∈ [L_hi−U_lo, U_hi−L_lo]；区间跨 0 即
资料无法确定方向。市场间只在两市场该价格的家庭样本都达到支持阈值
（`--support-threshold`，默认 10）时才可比较；north 市场没有 120/150 的任何
分配，显式标记禁止借用 east 的估计。另附「可退订金渠道」子区间（仅 deposit>0
且开放的报价），其上下界在本数据中重合为点估计，便于解释渠道作用。

### 4. 回执链与截止日重放（`app/finance.py`）

退款发出（refund_sent）使订单退出转化；银行迟到的 refund_reversed 通过
`replaces` 撤回原退款后，订单恢复为有效付款。基线中 O1-2：第 12 日退款生效、
第 14 日被撤回，因此 day13 的 150 价下界为 22.2%，day14 恢复为 27.8%，而分母
始终是 80。回执链断裂（无收款的退款、多笔生效退款）列为异常并进入对账。

### 5. 权利、替代邀约与同意（`app/rights.py`）

权利按 `recorded_day` 取截止日最新状态：retained 与 alternative_accepted 占用
目标场次；alternative_pending 在 `expires_day` 前持续扣减可放量、过期释放。
替代场次对应另一票种（product 不同）时，按条款必须征得家庭同意：待决、已留痕、
过期未决三种状态写入「替代邀约与跨票种同意」台账。

### 6. 可放容量与放量推荐（`app/capacity.py`）

```
可放新名额 = 物理容量 − 确认权利 − 已接受替代 − 未过期替代邀约 − 异常冻结
```

取消场次可放量为 0，只走改期/退款。每个场次 × 价格按需求区间反推报价数：
乐观量 ceil(席位/U)，保守量 ceil(席位/L)；L=0 时保守量标记「不可知，需先扩样」。
这解释了为什么价格变化会改变推荐放量——转化率区间随价格移动，反推出的报价数
随之变化。

### 7. 对账、售后与决策追溯（`app/reconcile.py`、`app/ledger.py`）

- 对账双向核对：在账收款但无权利（现金敞口，基线 24 笔共 480）、有权利但无
  在账收款（席位冻结，基线 R2/O1-8）。
- 售后工作台列出待确认替代、过期未决、改期已接受等待履约的家庭。
- 每次 `plan` 生成不可变 JSON 决策记录：六张输入表的 sha256 指纹、截止日、
  需求区间、容量行、推荐、对账差异与未清偿承诺。财务可凭版本号完整重现任一版
  决策所依据的付款、席位与承诺。

## 代码结构

| 模块 | 职责 |
|---|---|
| `app/loading.py` | 轮次导入、表头/引用校验、sha256 指纹 |
| `app/identity.py` | 时点并查集去重与事后链接审计 |
| `app/finance.py` | 回执链重放、净现金、异常识别 |
| `app/rights.py` | 权利时间线、替代邀约、跨票种同意 |
| `app/demand.py` | 四类状态、Manski 需求界、变价区间、共同支持 |
| `app/capacity.py` | 场次容量、冻结席位、放量推荐 |
| `app/reconcile.py` | 双向对账与售后工作清单 |
| `app/reports.py` | Markdown / CSV 渲染 |
| `app/ledger.py` | 决策版本台账 |
| `app/analysis.py` | 截止日分析流水线 |
| `app/cli.py` / `run.py` | 命令行入口与隔离启动器 |

## 基线轮次的主要结论（截止日 14）

| 市场 | 价格 | 家庭数 | 需求区间 | 订金渠道转化率 |
|---|---:|---:|---|---:|
| east | 90 | 18 | [27.8%, 94.4%] | 83.3% |
| east | 120 | 24 | [33.3%, 83.3%] | 66.7% |
| east | 150 | 18 | [27.8%, 61.1%] | 41.7% |
| north | 90 | 20 | [35.0%, 85.0%] | 70.0% |

- 三个变价对的 Δ 区间全部跨 0：现有资料只能给出需求范围，无法确定提价的方向效应。
- north 的 120/150 无共同支持，禁止借用 east 估计。
- S1 已取消（可放 0）；S2 可放 2 席（确认 2 + 已接受替代 3）；S3 可放 9 席。
- 现金敞口 480（24 笔在账订金无权利登记），R2 占 2 席但无在账收款，容量冻结。
