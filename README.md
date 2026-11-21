# 带订金预约的价格试探与兑现边界

观光服务报价试营、预约条款和银行回执的合成资料。 所有标识、金额、地名和观察值均为合成数据。

## 来源资料

- `sources/offers.csv`
- `sources/commerce_events.csv`
- `sources/identity_links.csv`
- `sources/sales_terms.csv`
- `sources/sessions.csv`
- `sources/booking_rights.csv`

offers 是按市场及试营业区段封存的完整分配表。price 和 deposit 为整单金额；deposit 为零表示只提供意向表，sold_out 是尝试中遇到暂停销售，且不改变原分组。identity_links 只在 learned_day 起对当时的经营人员可见。

commerce_events 每行是单独收到的回执。同一订单的退款发出与退款撤回各用独立行；replaces 引用被撤回的回执。sessions 按 session 和 learned_day 保留各次状态。

缺少 expires_day 的基础权利没有替代邀约到期时间。booking_rights 保留同一权利的往次状态，状态属于预约系统，到账和冲回另由银行回执记录。单个全价预约的容量以 guests 计。

## 本地检查

使用 Python 3.14，仅需标准库。在项目目录执行以下命令检查输入表结构、事件引用和基础记录覆盖。

```sh
python -m unittest discover -s tests -v
```
