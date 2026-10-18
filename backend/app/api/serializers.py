"""Turn ORM rows into JSON-ready dicts."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import permissions, workflow
from app.models import User, WorkItem
from app.permissions import CurrentUser


def _user_names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.display_name).where(User.id.in_(ids))).all())


def _item_dict(item: WorkItem, user: CurrentUser, names: dict[int, str]) -> dict:
    role = user.role_in(item.team_id)
    person = lambda uid: None if uid is None else {"id": uid, "name": names.get(uid)}  # noqa: E731
    return {
        "id": item.id,
        "team_id": item.team_id,
        "team_name": user.team_names.get(item.team_id),
        "title": item.title,
        "description": item.description,
        "type": item.type,
        "priority": item.priority,
        "status": item.status,
        "assignee": person(item.assignee_id),
        "created_by": person(item.created_by),
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "version": item.version,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        # What this user may do. Only used to show/hide buttons; the server re-checks every action.
        "can": {
            "edit": permissions.has_role(role, "edit") and item.status != "closed",
            "assign": permissions.has_role(role, "assign") and item.status != "closed",
            "claim": permissions.has_role(role, "claim") and item.assignee_id is None
                     and item.status in workflow.ACTIVE_STATUSES,
            "comment": permissions.has_role(role, "comment"),
            "transitions": workflow.allowed_transitions(role, user.id, item),
        },
    }


def serialize_item(db: Session, user: CurrentUser, item: WorkItem) -> dict:
    return serialize_items(db, user, [item])[0]


def serialize_items(db: Session, user: CurrentUser, items: list[WorkItem]) -> list[dict]:
    ids = {i.assignee_id for i in items if i.assignee_id} | {i.created_by for i in items}
    names = _user_names(db, ids)
    return [_item_dict(item, user, names) for item in items]


def me_dict(user: CurrentUser) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "teams": [{"id": tid, "name": user.team_names[tid], "role": role}
                  for tid, role in sorted(user.roles.items(), key=lambda kv: user.team_names[kv[0]])],
    }
