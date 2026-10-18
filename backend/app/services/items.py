"""Work item use-cases.

Rule for this module: functions check permissions and write rows, but never commit.
The API route commits once at the end, so an item change, its activity events and any
outbox job are saved together or not at all (history can never disagree with the item).
"""
import base64
from datetime import date, datetime

from sqlalchemy import func, or_, select, tuple_, update
from sqlalchemy.orm import Session

from app import permissions, workflow
from app.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.models import ActivityEvent, Comment, Job, Membership, Notification, User, WorkItem
from app.permissions import CurrentUser

VIEWS = ("assigned_to_me", "unassigned_urgent", "awaiting_my_approval")


# ---------------------------------------------------------------- helpers

def _as_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def record_change(db: Session, item_id: int, actor_id: int, field: str, old, new) -> None:
    db.add(ActivityEvent(item_id=item_id, actor_id=actor_id, event_type="changed",
                         field=field, from_value=_as_text(old), to_value=_as_text(new)))


def enqueue(db: Session, kind: str, payload: dict) -> None:
    """Transactional outbox: the job row commits or rolls back together with the change."""
    db.add(Job(kind=kind, payload=payload))


def _notify_assignee(db: Session, item: WorkItem, actor: CurrentUser, message: str) -> None:
    if item.assignee_id is not None and item.assignee_id != actor.id:
        enqueue(db, "notify_user", {
            "user_id": item.assignee_id,
            "item_id": item.id,
            "message": f"{actor.display_name} {message}: #{item.id} {item.title}",
        })


def load_item(db: Session, user: CurrentUser, item_id: int, action: str = "view"):
    """Fetch an item and check the user may perform `action` on it. Returns (item, role)."""
    item = db.get(WorkItem, item_id)
    if item is None:
        raise NotFound("Item not found")
    role = permissions.require(user, item.team_id, action)
    return item, role


def _reload(db: Session, item_id: int) -> WorkItem:
    """Re-read the latest committed row (used to send it back on a conflict)."""
    return db.execute(
        select(WorkItem).where(WorkItem.id == item_id).execution_options(populate_existing=True)
    ).scalar_one()


def _guarded_update(db: Session, item_id: int, guard: list, **values) -> WorkItem | None:
    """UPDATE work_items SET ..., version = version + 1 WHERE id = :id AND <guard> RETURNING *

    The guard (e.g. "version = 3" or "assignee_id IS NULL") is checked by Postgres while
    it holds the row lock, so two concurrent requests can never both pass it.
    Returns the updated item, or None when the guard did not match.
    """
    stmt = (
        update(WorkItem)
        .where(WorkItem.id == item_id, *guard)
        .values(**values, version=WorkItem.version + 1, updated_at=func.now())
        .returning(WorkItem)
        .execution_options(populate_existing=True)
    )
    return db.execute(stmt).scalar_one_or_none()


def _check_assignee(db: Session, team_id: int, assignee_id: int) -> None:
    role = db.scalar(select(Membership.role).where(
        Membership.team_id == team_id, Membership.user_id == assignee_id))
    if not permissions.has_role(role, "work"):
        raise ValidationFailed("The assignee must be a Member, Lead or Admin of the item's team")


def _version_conflict(db: Session, item_id: int) -> Conflict:
    return Conflict("This item was changed by someone else. Reload to see the latest version.",
                    code="version_conflict", current=_reload(db, item_id))


# ---------------------------------------------------------------- commands

def create_item(db: Session, user: CurrentUser, data) -> WorkItem:
    role = permissions.require(user, data.team_id, "create", resource="Team")
    if data.assignee_id is not None:
        if data.assignee_id != user.id and not permissions.has_role(role, "assign"):
            raise Forbidden("Only a Lead can assign an item to someone else")
        _check_assignee(db, data.team_id, data.assignee_id)

    item = WorkItem(team_id=data.team_id, title=data.title, description=data.description,
                    type=data.type, priority=data.priority, status="open",
                    assignee_id=data.assignee_id, created_by=user.id, due_date=data.due_date, version=1)
    db.add(item)
    db.flush()  # sends the INSERT so item.id exists
    db.add(ActivityEvent(item_id=item.id, actor_id=user.id, event_type="created", to_value="open"))
    if item.assignee_id is not None:
        record_change(db, item.id, user.id, "assignee", None, item.assignee_id)
        _notify_assignee(db, item, user, "assigned you")
    return item


def update_item(db: Session, user: CurrentUser, item_id: int, version: int, changes: dict) -> WorkItem:
    """Edit fields with optimistic locking: the client must send the version it last saw."""
    item, role = load_item(db, user, item_id, "edit")
    if item.version != version:
        raise Conflict("This item was changed by someone else. Reload to see the latest version.",
                       code="version_conflict", current=item)
    if item.status == "closed":
        raise Conflict("Closed items cannot be edited", code="item_closed")

    changes = {name: value for name, value in changes.items() if getattr(item, name) != value}
    if not changes:
        return item
    if "assignee_id" in changes:
        if not permissions.has_role(role, "assign"):
            raise Forbidden("Only a Lead can assign or reassign items")
        if changes["assignee_id"] is not None:
            _check_assignee(db, item.team_id, changes["assignee_id"])

    # `item` is exactly the row at `version`, so these are the true "before" values.
    before = {name: getattr(item, name) for name in changes}
    updated = _guarded_update(db, item_id, [WorkItem.version == version], **changes)
    if updated is None:  # someone committed a change between our read and our write
        raise _version_conflict(db, item_id)

    for name, old in before.items():
        record_change(db, item_id, user.id, "assignee" if name == "assignee_id" else name, old, changes[name])
    if "assignee_id" in changes:
        _notify_assignee(db, updated, user, "assigned you")
    return updated


def claim_item(db: Session, user: CurrentUser, item_id: int) -> WorkItem:
    """Take an unassigned item. If two people click at the same time, exactly one wins."""
    load_item(db, user, item_id, "claim")
    updated = _guarded_update(
        db, item_id,
        [WorkItem.assignee_id.is_(None), WorkItem.status.in_(workflow.ACTIVE_STATUSES)],
        assignee_id=user.id,
    )
    if updated is None:
        current = _reload(db, item_id)
        if current.assignee_id is not None:
            who = "you" if current.assignee_id == user.id else "someone else"
            raise Conflict(f"Already claimed by {who}", code="already_claimed", current=current)
        raise Conflict(f"Items in status '{current.status}' cannot be claimed", code="not_claimable",
                       current=current)
    record_change(db, item_id, user.id, "assignee", None, user.id)
    return updated


def transition_item(db: Session, user: CurrentUser, item_id: int, to_status: str, version: int) -> WorkItem:
    item, role = load_item(db, user, item_id, "view")
    if item.version != version:
        raise Conflict("This item was changed by someone else. Reload to see the latest version.",
                       code="version_conflict", current=item)
    workflow.check_transition(role, user.id, item, to_status)

    from_status = item.status
    updated = _guarded_update(db, item_id, [WorkItem.version == version], status=to_status)
    if updated is None:
        raise _version_conflict(db, item_id)
    record_change(db, item_id, user.id, "status", from_status, to_status)

    if to_status == "awaiting_approval":
        enqueue(db, "notify_approvers", {
            "item_id": item_id,
            "team_id": updated.team_id,
            "exclude_user_id": updated.assignee_id,
            "message": f"#{item_id} {updated.title} is waiting for your approval",
        })
    elif from_status == "awaiting_approval":
        verb = "approved" if to_status == "resolved" else "sent back"
        _notify_assignee(db, updated, user, f"{verb} your item")
    return updated


def add_comment(db: Session, user: CurrentUser, item_id: int, body: str) -> Comment:
    """Comments do not bump the item version: talking about an item is not editing it."""
    load_item(db, user, item_id, "comment")
    comment = Comment(item_id=item_id, author_id=user.id, body=body)
    db.add(comment)
    db.flush()
    db.add(ActivityEvent(item_id=item_id, actor_id=user.id, event_type="commented", comment_id=comment.id))
    return comment


# ---------------------------------------------------------------- queries

def encode_cursor(item: WorkItem) -> str:
    raw = f"{item.created_at.isoformat()}|{item.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        created_at, item_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(created_at), int(item_id)
    except ValueError:
        raise ValidationFailed("Invalid cursor") from None


def list_items(db: Session, user: CurrentUser, *, view=None, team_id=None, statuses=(), priorities=(),
               types=(), assignee=None, q=None, cursor=None, limit=25):
    """Filtered, keyset-paginated list. Only ever returns items from the user's teams.

    Keyset pagination: "give me 25 items older than (created_at, id) of the last one I saw".
    Unlike OFFSET, it stays fast on page 1000 and never skips/duplicates rows when new
    items are inserted while someone is paging.
    """
    team_ids = list(user.roles)
    if team_id is not None:
        team_ids = [team_id] if team_id in user.roles else []
    stmt = select(WorkItem).where(WorkItem.team_id.in_(team_ids))

    if view == "assigned_to_me":
        stmt = stmt.where(WorkItem.assignee_id == user.id, WorkItem.status.not_in(workflow.DONE_STATUSES))
    elif view == "unassigned_urgent":
        stmt = stmt.where(WorkItem.assignee_id.is_(None), WorkItem.priority == "urgent",
                          WorkItem.status.in_(workflow.ACTIVE_STATUSES))
    elif view == "awaiting_my_approval":
        approver_teams = [t for t, role in user.roles.items() if permissions.has_role(role, "approve")]
        stmt = stmt.where(WorkItem.team_id.in_(approver_teams), WorkItem.status == "awaiting_approval",
                          or_(WorkItem.assignee_id.is_(None), WorkItem.assignee_id != user.id))

    if statuses:
        stmt = stmt.where(WorkItem.status.in_(statuses))
    if priorities:
        stmt = stmt.where(WorkItem.priority.in_(priorities))
    if types:
        stmt = stmt.where(WorkItem.type.in_(types))
    if assignee == "me":
        stmt = stmt.where(WorkItem.assignee_id == user.id)
    elif assignee == "none":
        stmt = stmt.where(WorkItem.assignee_id.is_(None))
    elif assignee:
        if not assignee.isdigit():
            raise ValidationFailed("assignee must be 'me', 'none' or a user id")
        stmt = stmt.where(WorkItem.assignee_id == int(assignee))
    if q:
        stmt = stmt.where(WorkItem.search_vector.op("@@")(func.websearch_to_tsquery("english", q)))
    if cursor:
        created_at, last_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(WorkItem.created_at, WorkItem.id) < tuple_(created_at, last_id))

    rows = db.scalars(stmt.order_by(WorkItem.created_at.desc(), WorkItem.id.desc()).limit(limit + 1)).all()
    next_cursor = encode_cursor(rows[limit - 1]) if len(rows) > limit else None
    return rows[:limit], next_cursor


def list_activity(db: Session, user: CurrentUser, item_id: int, before: int | None = None, limit: int = 50):
    """History newest-first, paginated with `before` = the smallest event id already shown."""
    load_item(db, user, item_id, "view")
    stmt = (
        select(ActivityEvent, User.display_name, Comment.body)
        .join(User, User.id == ActivityEvent.actor_id)
        .outerjoin(Comment, Comment.id == ActivityEvent.comment_id)
        .where(ActivityEvent.item_id == item_id)
    )
    if before is not None:
        stmt = stmt.where(ActivityEvent.id < before)
    rows = db.execute(stmt.order_by(ActivityEvent.id.desc()).limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    # Assignee changes store user ids; look the names up in one query.
    user_ids = {int(v) for event, _, _ in rows if event.field == "assignee"
                for v in (event.from_value, event.to_value) if v}
    names = dict(db.execute(select(User.id, User.display_name).where(User.id.in_(user_ids))).all()) if user_ids else {}

    def label(event, value):
        if value is not None and event.field == "assignee":
            return names.get(int(value), f"user {value}")
        return value

    events = [{
        "id": event.id,
        "type": event.event_type,
        "actor": actor_name,
        "field": event.field,
        "from": label(event, event.from_value),
        "to": label(event, event.to_value),
        "comment": comment_body,
        "created_at": event.created_at.isoformat(),
    } for event, actor_name, comment_body in rows]
    return events, (rows[-1][0].id if has_more else None)


def list_team_members(db: Session, user: CurrentUser, team_id: int) -> list[dict]:
    permissions.require(user, team_id, "view", resource="Team")
    rows = db.execute(
        select(User.id, User.display_name, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.team_id == team_id)
        .order_by(User.display_name)
    ).all()
    return [{"id": r.id, "name": r.display_name, "role": r.role} for r in rows]


def list_notifications(db: Session, user: CurrentUser, limit: int = 20) -> list[dict]:
    rows = db.scalars(
        select(Notification).where(Notification.user_id == user.id)
        .order_by(Notification.id.desc()).limit(limit)
    ).all()
    return [{"id": n.id, "item_id": n.item_id, "message": n.message, "created_at": n.created_at.isoformat()}
            for n in rows]
