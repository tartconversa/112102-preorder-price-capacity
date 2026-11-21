"""预约权利时间线、替代邀约与同意规则。

booking_rights 保留同一权利的往次状态（状态属于预约系统），到账与冲回另由
银行回执记录。sales_terms 决定一项订金到底保住了什么：
- preserves_price: 只保原价
- keeps_party: 承诺同行人同一场次
- replacement_requires_consent: 替代场次（尤其跨票种）须征得家庭同意

替代邀约在 expires_day 前未接受/拒绝时持续占用可放容量；过期后释放。
"""

from __future__ import annotations

from dataclasses import dataclass

# 权利状态（同一权利按 recorded_day 保留历史行）。
RETAINED = "retained"
ALTERNATIVE_PENDING = "alternative_pending"
ALTERNATIVE_ACCEPTED = "alternative_accepted"
ALTERNATIVE_DECLINED = "alternative_declined"


@dataclass(frozen=True)
class Right:
    right_id: str
    order_id: str
    term: str
    session: str
    guests: int
    state: str
    recorded_day: int
    alternative: str
    expires_day: int | None


@dataclass(frozen=True)
class Term:
    term: str
    product: str
    preserves_price: bool
    keeps_party: bool
    replacement_requires_consent: bool


def parse_terms(rows: list[dict[str, object]]) -> dict[str, Term]:
    return {
        str(r["term"]): Term(
            term=str(r["term"]), product=str(r["product"]),
            preserves_price=str(r["preserves_price"]) == "yes",
            keeps_party=str(r["keeps_party"]) == "yes",
            replacement_requires_consent=bool(
                str(r["replacement_requires_consent"]) == "yes"),
        )
        for r in rows
    }


def parse_rights(rows: list[dict[str, object]]) -> list[Right]:
    rights = [
        Right(
            right_id=str(r["right"]), order_id=str(r["order"]),
            term=str(r["term"]), session=str(r["session"]),
            guests=int(r["guests"]), state=str(r["state"]),
            recorded_day=int(r["recorded_day"]),
            alternative=str(r["alternative"]),
            expires_day=int(r["expires_day"]) if r["expires_day"] not in (None, "") else None,
        )
        for r in rows
    ]
    rights.sort(key=lambda x: (x.right_id, x.recorded_day))
    return rights


def current_state(right_id: str, rights: list[Right], as_of_day: int) -> Right | None:
    """权利在截止日的最新状态行。"""

    history = [r for r in rights
               if r.right_id == right_id and r.recorded_day <= as_of_day]
    return history[-1] if history else None


def all_current_rights(rights: list[Right], as_of_day: int) -> list[Right]:
    """每个权利在截止日的最新行。"""

    latest: dict[str, Right] = {}
    for right in rights:
        if right.recorded_day <= as_of_day:
            latest[right.right_id] = right
    return [latest[key] for key in sorted(latest)]


@dataclass(frozen=True)
class ConsentIssue:
    right_id: str
    order_id: str
    code: str
    from_product: str
    to_product: str
    detail: str


def check_alternative_consent(rights: list[Right], terms: dict[str, Term],
                              sessions: list[dict[str, object]],
                              as_of_day: int) -> list[ConsentIssue]:
    """替代场次的票种与同意校验。

    替代场次可容纳人数够、但对应的是另一票种（product 不同）时，
    只能在征得同意后使用。数据中 alternative_accepted 即视为已取得同意的
    留痕；alternative_pending 的跨票种邀约在接受前属于“待取得同意”。
    """

    session_product = {str(r["session"]): str(r["product"]) for r in sessions}
    issues: list[ConsentIssue] = []
    for right in all_current_rights(rights, as_of_day):
        if not right.alternative:
            continue
        term = terms.get(right.term)
        if term is None or not term.replacement_requires_consent:
            continue
        original_product = session_product.get(right.session, "")
        alt_product = session_product.get(right.alternative, "")
        # 原场次可能已取消/不在最新快照，退回条款票种作为原票种。
        if not original_product:
            original_product = term.product
        if original_product == alt_product:
            continue
        if right.state == ALTERNATIVE_ACCEPTED:
            issues.append(ConsentIssue(
                right.right_id, right.order_id, "cross_product_consent_recorded",
                original_product, alt_product,
                f"跨票种替代（{original_product}→{alt_product}）已征得家庭同意并接受"))
        elif right.state == ALTERNATIVE_PENDING:
            expired = (right.expires_day is not None
                       and right.expires_day < as_of_day)
            issues.append(ConsentIssue(
                right.right_id, right.order_id,
                "cross_product_consent_pending" if not expired
                else "cross_product_consent_unresolved",
                original_product, alt_product,
                "跨票种替代邀约待家庭同意，未同意前不得占用" if not expired
                else "跨票种替代邀约已过期且未取得同意，需重新安排"))
    return issues


@dataclass(frozen=True)
class SessionHold:
    """一个场次在截止日被占用的席位。"""

    session: str
    guests: int
    right_id: str
    order_id: str
    kind: str  # confirmed | alternative_pending | alternative_accepted


def holds_as_of(rights: list[Right], as_of_day: int) -> list[SessionHold]:
    """汇总截止日各场地上的有效占用。

    - retained/accepted：确认占用（accepted 落在替代场地上）
    - alternative_pending：已发出但未过期的替代邀约，继续扣减可放量
    - 邀约过期（expires_day < as_of_day）后释放
    """

    holds: list[SessionHold] = []
    for right in all_current_rights(rights, as_of_day):
        if right.state == RETAINED:
            holds.append(SessionHold(right.session, right.guests,
                                     right.right_id, right.order_id, "confirmed"))
        elif right.state == ALTERNATIVE_ACCEPTED:
            target = right.alternative or right.session
            holds.append(SessionHold(target, right.guests,
                                     right.right_id, right.order_id,
                                     "alternative_accepted"))
        elif right.state == ALTERNATIVE_PENDING:
            if right.expires_day is None or right.expires_day >= as_of_day:
                target = right.alternative or right.session
                holds.append(SessionHold(target, right.guests,
                                         right.right_id, right.order_id,
                                         "alternative_pending"))
        # alternative_declined / 已过期的 pending：不占用
    return holds
