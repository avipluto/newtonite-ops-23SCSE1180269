"""HTTP endpoints. Thin on purpose: parse the request, call a service, commit once."""
from typing import Callable

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.schemas import CommentIn, ItemCreate, ItemType, ItemUpdate, LoginIn, Priority, Status, TransitionIn, View
from app.api.serializers import me_dict, serialize_item, serialize_items
from app.db import get_db
from app.errors import AppError, Unauthorized
from app.models import WorkItem
from app.permissions import CurrentUser
from app.services import auth, idempotency
from app.services import items as svc

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                 db: Session = Depends(get_db)) -> CurrentUser:
    if creds is None:
        raise Unauthorized("Missing bearer token")
    return auth.user_from_token(db, creds.credentials)


def mutate(db: Session, user: CurrentUser, action: Callable[[], dict], *, status_code: int = 200,
           idempotency_key: str | None = None, fingerprint: str | None = None) -> JSONResponse:
    """Run one write as ONE transaction and return its JSON.

    - With an Idempotency-Key, a repeated request gets the stored response instead of running again.
    - If the action raises a conflict carrying the latest item, attach it as JSON for the client.
    - Nothing is committed unless the action succeeds.
    """
    if idempotency_key:
        stored = idempotency.begin(db, user.id, idempotency_key, fingerprint)
        if stored is not None:
            return JSONResponse(stored.response_body, status_code=stored.response_status,
                                headers={"Idempotent-Replayed": "true"})
    try:
        body = action()
    except AppError as err:
        if isinstance(err.current, WorkItem):
            err.current = serialize_item(db, user, err.current)
        raise
    if idempotency_key:
        idempotency.finish(db, user.id, idempotency_key, status_code, body)
    db.commit()
    return JSONResponse(body, status_code=status_code)


# ---------------------------------------------------------------- auth

@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    token, user = auth.login(db, body.username, body.password)
    db.commit()
    return {"token": token, "user": me_dict(auth.load_current_user(db, user))}


@router.post("/auth/logout", status_code=204)
def logout(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)):
    if creds is not None:
        auth.logout(db, creds.credentials)
        db.commit()


@router.get("/me")
def me(user: CurrentUser = Depends(current_user)):
    return me_dict(user)


@router.get("/teams/{team_id}/members")
def team_members(team_id: int, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return svc.list_team_members(db, user, team_id)


@router.get("/notifications")
def notifications(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return svc.list_notifications(db, user)


# ---------------------------------------------------------------- items

@router.get("/items")
def list_items(
    view: View | None = None,
    team_id: int | None = None,
    status: list[Status] = Query(default=[]),
    priority: list[Priority] = Query(default=[]),
    type: list[ItemType] = Query(default=[]),
    assignee: str | None = Query(default=None, description="'me', 'none' or a user id"),
    q: str | None = Query(default=None, max_length=200),
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows, next_cursor = svc.list_items(db, user, view=view, team_id=team_id, statuses=status,
                                       priorities=priority, types=type, assignee=assignee,
                                       q=q or None, cursor=cursor, limit=limit)
    return {"items": serialize_items(db, user, rows), "next_cursor": next_cursor}


@router.post("/items", status_code=201)
def create_item(body: ItemCreate, idempotency_key: str | None = Header(default=None, max_length=100),
                user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return mutate(
        db, user, lambda: serialize_item(db, user, svc.create_item(db, user, body)),
        status_code=201, idempotency_key=idempotency_key,
        fingerprint=idempotency.fingerprint("POST", "/items", body.model_dump(mode="json")),
    )


@router.get("/items/{item_id}")
def get_item(item_id: int, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    item, _ = svc.load_item(db, user, item_id, "view")
    return serialize_item(db, user, item)


@router.patch("/items/{item_id}")
def update_item(item_id: int, body: ItemUpdate, user: CurrentUser = Depends(current_user),
                db: Session = Depends(get_db)):
    changes = body.changes()
    return mutate(db, user, lambda: serialize_item(db, user, svc.update_item(db, user, item_id, body.version, changes)))


@router.post("/items/{item_id}/claim")
def claim_item(item_id: int, idempotency_key: str | None = Header(default=None, max_length=100),
               user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return mutate(
        db, user, lambda: serialize_item(db, user, svc.claim_item(db, user, item_id)),
        idempotency_key=idempotency_key,
        fingerprint=idempotency.fingerprint("POST", f"/items/{item_id}/claim", None),
    )


@router.post("/items/{item_id}/transition")
def transition_item(item_id: int, body: TransitionIn,
                    idempotency_key: str | None = Header(default=None, max_length=100),
                    user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return mutate(
        db, user,
        lambda: serialize_item(db, user, svc.transition_item(db, user, item_id, body.to_status, body.version)),
        idempotency_key=idempotency_key,
        fingerprint=idempotency.fingerprint("POST", f"/items/{item_id}/transition", body.model_dump()),
    )


@router.get("/items/{item_id}/activity")
def item_activity(item_id: int, before: int | None = None, limit: int = Query(default=50, ge=1, le=200),
                  user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    events, next_before = svc.list_activity(db, user, item_id, before=before, limit=limit)
    return {"events": events, "next_before": next_before}


@router.post("/items/{item_id}/comments", status_code=201)
def add_comment(item_id: int, body: CommentIn, idempotency_key: str | None = Header(default=None, max_length=100),
                user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    def action():
        comment = svc.add_comment(db, user, item_id, body.body)
        return {"id": comment.id, "item_id": item_id, "body": comment.body}

    return mutate(
        db, user, action, status_code=201, idempotency_key=idempotency_key,
        fingerprint=idempotency.fingerprint("POST", f"/items/{item_id}/comments", body.model_dump()),
    )
