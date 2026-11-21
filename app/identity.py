"""时点去重。

规则（对应经营约束）：分组时只能见到家庭 *当时* 的去重关系。
identity_links 仅在 ``learned_day`` 当天起对经营人员可见，事后补来的合并证据
不能把分在不同价格条件/组里的两个账号强行归并。

本模块在每个 block 的分配日（assigned_day）用当日已可见的链接做并查集，
为该 block 内的账号生成稳定的家庭组件编号；晚于分配日出现的链接只进审计清单。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Link:
    link_id: str
    left: str
    right: str
    learned_day: int


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # 稳定排序，保证组件代表元在不同运行间确定。
            if ra < rb:
                self._parent[rb] = ra
            else:
                self._parent[ra] = rb


def parse_links(rows: list[dict[str, object]]) -> list[Link]:
    return [
        Link(str(r["link"]), str(r["left_account"]), str(r["right_account"]),
             int(r["learned_day"]))
        for r in rows
    ]


def components_for_day(links: list[Link], accounts: list[str],
                       as_of_day: int) -> dict[str, str]:
    """返回账号 -> 组件代表元，仅采用 ``learned_day <= as_of_day`` 的链接。"""

    uf = _UnionFind()
    for account in accounts:
        uf.find(account)
    for link in links:
        if link.learned_day <= as_of_day:
            uf.union(link.left, link.right)
    return {account: uf.find(account) for account in accounts}


@dataclass(frozen=True)
class BlockDedup:
    block: str
    assigned_day: int
    components: dict[str, str]
    late_links: tuple[Link, ...]


def dedup_by_block(offer_rows: list[dict[str, object]],
                   links: list[Link]) -> dict[str, BlockDedup]:
    """逐 block 按分配日做时点去重。"""

    result: dict[str, BlockDedup] = {}
    blocks: dict[str, list[dict[str, object]]] = {}
    for row in offer_rows:
        blocks.setdefault(str(row["block"]), []).append(row)
    for block, rows in sorted(blocks.items()):
        assigned_day = min(int(r["assigned_day"]) for r in rows)
        # 数据约定同一 block 只有一个分配日；此处显式校验。
        if any(int(r["assigned_day"]) != assigned_day for r in rows):
            raise ValueError(f"block {block} 存在多个分配日，无法锁定分组时点")
        accounts = sorted({str(r["account"]) for r in rows})
        components = components_for_day(links, accounts, assigned_day)
        late = tuple(
            link for link in links
            if link.learned_day > assigned_day
            and (link.left in components or link.right in components)
        )
        result[block] = BlockDedup(block, assigned_day, components, late)
    return result


def late_link_audit(offer_rows: list[dict[str, object]],
                    links: list[Link]) -> list[dict[str, object]]:
    """审计行：每条事后链接及其原本会跨越的 block / 价格条件。"""

    account_offer = {str(r["account"]): r for r in offer_rows}
    audit: list[dict[str, object]] = []
    for link in sorted(links, key=lambda x: x.learned_day):
        left = account_offer.get(link.left)
        right = account_offer.get(link.right)
        audit.append({
            "link": link.link_id,
            "left_account": link.left,
            "right_account": link.right,
            "learned_day": link.learned_day,
            "left_block": str(left["block"]) if left else "",
            "right_block": str(right["block"]) if right else "",
            "left_price": int(left["price"]) if left else None,
            "right_price": int(right["price"]) if right else None,
            "would_merge_cells": bool(
                left and right
                and (left["block"] != right["block"]
                     or int(left["price"]) != int(right["price"]))
            ),
        })
    return audit
