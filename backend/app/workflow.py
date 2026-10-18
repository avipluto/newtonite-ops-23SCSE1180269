"""Work item workflow: which status changes exist and who may make them.

    open --start--> in_progress --submit--> awaiting_approval --approve--> resolved --close--> closed
                        ^                          |                          |
                        +---------- reject --------+                          |
                        +------------------------- reopen --------------------+

Anything not in TRANSITIONS (for example open -> closed) is rejected by the server.
"""
from dataclasses import dataclass

from app.errors import Conflict, Forbidden
from app.permissions import has_role

STATUSES = ("open", "in_progress", "awaiting_approval", "resolved", "closed")
ACTIVE_STATUSES = ("open", "in_progress")  # can be claimed; count as "needs work"
DONE_STATUSES = ("resolved", "closed")


@dataclass(frozen=True)
class Transition:
    label: str
    # "assignee": the item's assignee, or a Lead/Admin of the team
    # "approver": a Lead/Admin of the team who is NOT the assignee
    # "member":   any Member/Lead/Admin of the team
    who: str


TRANSITIONS = {
    ("open", "in_progress"): Transition("Start work", "assignee"),
    ("in_progress", "awaiting_approval"): Transition("Submit for approval", "assignee"),
    ("awaiting_approval", "resolved"): Transition("Approve", "approver"),
    ("awaiting_approval", "in_progress"): Transition("Reject (send back)", "approver"),
    ("resolved", "closed"): Transition("Close", "member"),
    ("resolved", "in_progress"): Transition("Reopen", "member"),
}


def check_transition(role: str | None, actor_id: int, item, to_status: str) -> Transition:
    """Raise if this user may not move this item to to_status; otherwise return the rule."""
    rule = TRANSITIONS.get((item.status, to_status))
    if rule is None:
        raise Conflict(f"Cannot move an item from {item.status} to {to_status}", code="invalid_transition")

    if rule.who == "approver":
        if not has_role(role, "approve"):
            raise Forbidden("Only a Lead or Admin of this team can approve or reject")
        if item.assignee_id == actor_id:
            raise Forbidden("You cannot approve your own work; another Lead must do it")
    elif rule.who == "assignee":
        if not has_role(role, "work"):
            raise Forbidden("Viewers cannot change the status of items")
        if item.assignee_id is None:
            raise Conflict("This item has no assignee; claim or assign it first", code="no_assignee")
        if item.assignee_id != actor_id and not has_role(role, "assign"):
            raise Forbidden("Only the assignee or a Lead can do this")
    else:  # "member"
        if not has_role(role, "work"):
            raise Forbidden("Viewers cannot change the status of items")
    return rule


def allowed_transitions(role: str | None, actor_id: int, item) -> list[dict]:
    """The moves this user could make right now. Used only to decide which buttons to show;
    the server re-checks with check_transition() on every request."""
    allowed = []
    for (from_status, to_status), rule in TRANSITIONS.items():
        if from_status != item.status:
            continue
        try:
            check_transition(role, actor_id, item, to_status)
        except (Conflict, Forbidden):
            continue
        allowed.append({"to": to_status, "label": rule.label})
    return allowed
