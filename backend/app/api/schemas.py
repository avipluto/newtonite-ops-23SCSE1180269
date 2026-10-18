"""Request bodies, validated by Pydantic before any of our code runs."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.errors import ValidationFailed

ItemType = Literal["incident", "customer_issue", "payment", "compliance", "task"]
Priority = Literal["low", "medium", "high", "urgent"]
Status = Literal["open", "in_progress", "awaiting_approval", "resolved", "closed"]
View = Literal["assigned_to_me", "unassigned_urgent", "awaiting_my_approval"]


def not_blank(value: str | None) -> str | None:
    if value is None:
        return value
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class LoginIn(BaseModel):
    username: str
    password: str


class ItemCreate(BaseModel):
    team_id: int
    title: str = Field(max_length=200)
    description: str = Field(default="", max_length=10_000)
    type: ItemType
    priority: Priority = "medium"
    assignee_id: int | None = None
    due_date: date | None = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        return not_blank(value)


class ItemUpdate(BaseModel):
    """Send `version` plus only the fields you want to change. `"assignee_id": null` unassigns."""
    version: int
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    type: ItemType | None = None
    priority: Priority | None = None
    assignee_id: int | None = None
    due_date: date | None = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        return not_blank(value)

    def changes(self) -> dict:
        """Only the fields the client actually sent."""
        data = self.model_dump(exclude_unset=True, exclude={"version"})
        for name in ("title", "description", "type", "priority"):
            if name in data and data[name] is None:
                raise ValidationFailed(f"{name} cannot be null")
        return data


class TransitionIn(BaseModel):
    to_status: Status
    version: int


class CommentIn(BaseModel):
    body: str = Field(max_length=5000)

    @field_validator("body")
    @classmethod
    def body_not_blank(cls, value):
        return not_blank(value)
