"""Login, logout and turning a bearer token into the current user."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import config
from app.errors import Unauthorized
from app.models import AuthToken, Membership, Team, User
from app.permissions import CurrentUser
from app.security import hash_token, new_token, verify_password


def login(db: Session, username: str, password: str) -> tuple[str, User]:
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not verify_password(password, user.password_hash):
        raise Unauthorized("Invalid username or password")
    token = new_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=config.TOKEN_TTL_HOURS)
    db.add(AuthToken(token_hash=hash_token(token), user_id=user.id, expires_at=expires_at))
    return token, user


def logout(db: Session, token: str) -> None:
    db.execute(delete(AuthToken).where(AuthToken.token_hash == hash_token(token)))


def user_from_token(db: Session, token: str) -> CurrentUser:
    user = db.scalar(
        select(User)
        .join(AuthToken, AuthToken.user_id == User.id)
        .where(AuthToken.token_hash == hash_token(token), AuthToken.expires_at > func.now())
    )
    if user is None:
        raise Unauthorized("Invalid or expired token")
    return load_current_user(db, user)


def load_current_user(db: Session, user: User) -> CurrentUser:
    rows = db.execute(
        select(Membership.team_id, Membership.role, Team.name)
        .join(Team, Team.id == Membership.team_id)
        .where(Membership.user_id == user.id)
    ).all()
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        roles={row.team_id: row.role for row in rows},
        team_names={row.team_id: row.name for row in rows},
    )
