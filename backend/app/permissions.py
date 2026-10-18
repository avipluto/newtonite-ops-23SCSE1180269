"""Authorization: who may do what, per team.

Every endpoint that touches an item calls require() with the item's team. This is
the single place where roles are checked; the frontend only hides buttons.

    Role     view  comment/create/edit/claim/work  assign  approve
    viewer    yes        no                          no      no
    member    yes        yes                         no      no
    lead      yes        yes                         yes     yes
    admin     yes        yes                         yes     yes
"""
from dataclasses import dataclass, field

from app.errors import Forbidden, NotFound

ROLE_RANK = {"viewer": 1, "member": 2, "lead": 3, "admin": 4}

# Minimum role a user needs in the item's team for each action.
MIN_ROLE = {
    "view": "viewer",
    "comment": "member",
    "create": "member",
    "edit": "member",
    "claim": "member",
    "work": "member",     # start / submit / close / reopen (see workflow.py)
    "assign": "lead",     # assign or reassign an item to someone
    "approve": "lead",    # pass or reject the approval step
}


@dataclass
class CurrentUser:
    """The logged-in user plus their role in each team (loaded once per request)."""
    id: int
    username: str
    display_name: str
    roles: dict[int, str]                               # team_id -> role
    team_names: dict[int, str] = field(default_factory=dict)

    def role_in(self, team_id: int) -> str | None:
        return self.roles.get(team_id)


def has_role(role: str | None, action: str) -> bool:
    return role is not None and ROLE_RANK[role] >= ROLE_RANK[MIN_ROLE[action]]


def require(user: CurrentUser, team_id: int, action: str, resource: str = "Item") -> str:
    """Return the user's role in the team, or raise.

    Not in the team at all  -> 404, so we never reveal that the item exists.
    In the team, role too low -> 403 with the reason.
    """
    role = user.role_in(team_id)
    if role is None:
        raise NotFound(f"{resource} not found")
    if not has_role(role, action):
        raise Forbidden(f"Your role in this team ({role}) is not allowed to {action} items")
    return role
