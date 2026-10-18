"""Demo data: 4 teams, 10 users with different roles, ~5,000 work items with history.

    python -m app.seed           # seeds only if the database has no users yet
    python -m app.seed --reset   # wipes all data and seeds again

All demo users share the password DEMO_PASSWORD (demo only!).
"""
import random
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, insert, select, text

from app.db import SessionLocal
from app.models import ActivityEvent, Membership, Team, User, WorkItem
from app.security import hash_password

DEMO_PASSWORD = "demo1234"
ITEM_COUNT = 5000

TEAMS = ["Payments Ops", "Customer Support", "Compliance", "Platform"]

# username, display name, {team: role}
USERS = [
    ("ada", "Ada Fernandes", {"Payments Ops": "admin", "Customer Support": "admin",
                              "Compliance": "admin", "Platform": "admin"}),
    ("priya", "Priya Sharma", {"Payments Ops": "lead", "Compliance": "member"}),
    ("sam", "Sam Okafor", {"Customer Support": "lead", "Payments Ops": "lead"}),
    ("lin", "Lin Chen", {"Compliance": "lead", "Platform": "lead"}),
    ("maya", "Maya Patel", {"Payments Ops": "member"}),
    ("omar", "Omar Haddad", {"Payments Ops": "member", "Customer Support": "member"}),
    ("jules", "Jules Martin", {"Customer Support": "member"}),
    ("kai", "Kai Nakamura", {"Platform": "member", "Compliance": "member"}),
    ("noah", "Noah Becker", {"Platform": "member"}),
    ("vera", "Vera Ivanova", {"Payments Ops": "viewer", "Customer Support": "viewer"}),
]

TEAM_TYPES = {
    "Payments Ops": ["payment", "payment", "incident", "task"],
    "Customer Support": ["customer_issue", "customer_issue", "incident", "task"],
    "Compliance": ["compliance", "compliance", "task"],
    "Platform": ["incident", "incident", "task"],
}

TITLES = {
    "payment": ["Refund stuck in pending for order {n}", "Settlement mismatch for merchant {n}",
                "Chargeback dispute #{n} needs evidence", "Duplicate debit reported on txn {n}"],
    "incident": ["Checkout latency spike in region {r}", "Card payments failing for {r} merchants",
                 "Database CPU at 95% on db-{n}", "Webhook deliveries delayed ({n} queued)"],
    "customer_issue": ["Customer cannot reset password (ticket {n})", "Invoice {n} shows wrong tax amount",
                       "Account {n} locked after KYC update", "Customer requests data export ({n})"],
    "compliance": ["KYC review for business account {n}", "Quarterly AML report section {n}",
                   "Sanctions screening hit on payee {n}", "Data retention audit for system {n}"],
    "task": ["Update runbook for on-call rotation {n}", "Rotate API credentials for partner {n}",
             "Clean up stale feature flags ({n})", "Review alert thresholds for service {n}"],
}
REGIONS = ["EU", "US-East", "India", "APAC", "UK"]
STATUS_PATH = ["open", "in_progress", "awaiting_approval", "resolved", "closed"]


def _reset(db) -> None:
    db.execute(text(
        "TRUNCATE notifications, jobs, idempotency_keys, activity_events, comments, work_items, "
        "auth_tokens, memberships, teams, users RESTART IDENTITY CASCADE"))


def seed(reset: bool = False) -> None:
    rng = random.Random(42)  # deterministic: same data every time
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        if reset:
            _reset(db)
        elif db.scalar(select(func.count()).select_from(User)):
            print("Database already has data; skipping seed (use --reset to start over).")
            return

        team_ids = {}
        for name in TEAMS:
            team = Team(name=name)
            db.add(team)
            db.flush()
            team_ids[name] = team.id

        password_hash = hash_password(DEMO_PASSWORD)
        user_ids = {}
        workers = {name: [] for name in TEAMS}      # member/lead/admin per team
        approvers = {name: [] for name in TEAMS}    # lead/admin per team
        for username, display_name, roles in USERS:
            user = User(username=username, display_name=display_name, password_hash=password_hash)
            db.add(user)
            db.flush()
            user_ids[username] = user.id
            for team_name, role in roles.items():
                db.add(Membership(user_id=user.id, team_id=team_ids[team_name], role=role))
                if role != "viewer":
                    workers[team_name].append(user.id)
                if role in ("lead", "admin"):
                    approvers[team_name].append(user.id)
        db.flush()

        items, histories = [], []
        for n in range(ITEM_COUNT):
            team_name = rng.choice(TEAMS)
            item_type = rng.choice(TEAM_TYPES[team_name])
            status = rng.choices(STATUS_PATH, weights=[25, 25, 10, 15, 25])[0]
            priority = rng.choices(["low", "medium", "high", "urgent"], weights=[25, 40, 25, 10])[0]
            # At least 7 days ago, so the generated history (at most ~6 days long) never lands in the future.
            created_at = now - timedelta(days=rng.uniform(7, 120))
            creator = rng.choice(workers[team_name])
            assignee = None
            if status != "open" or rng.random() < 0.5:
                assignee = rng.choice(workers[team_name])
            title = rng.choice(TITLES[item_type]).format(n=rng.randint(1000, 99999), r=rng.choice(REGIONS))

            # Build the history this item would have had: created, assigned, then each status step.
            t = created_at
            events = [dict(actor_id=creator, event_type="created", field=None, from_value=None,
                           to_value="open", created_at=t)]
            if assignee is not None:
                t += timedelta(minutes=rng.randint(5, 600))
                events.append(dict(actor_id=assignee, event_type="changed", field="assignee",
                                   from_value=None, to_value=str(assignee), created_at=t))
            for step in range(STATUS_PATH.index(status)):
                t += timedelta(minutes=rng.randint(10, 2000))
                frm, to = STATUS_PATH[step], STATUS_PATH[step + 1]
                actor = assignee
                if to == "resolved":  # approval must come from a lead who is not the assignee
                    actor = rng.choice([a for a in approvers[team_name] if a != assignee] or approvers[team_name])
                events.append(dict(actor_id=actor, event_type="changed", field="status",
                                   from_value=frm, to_value=to, created_at=t))

            items.append(dict(
                team_id=team_ids[team_name], title=title,
                description=f"Seeded {item_type.replace('_', ' ')} for the {team_name} team.",
                type=item_type, priority=priority, status=status, assignee_id=assignee,
                created_by=creator,
                due_date=(created_at + timedelta(days=rng.randint(1, 30))).date() if rng.random() < 0.6 else None,
                version=len(events), created_at=created_at, updated_at=t,
            ))
            histories.append(events)

        # A few hand-made items on top of the list, for a predictable demo.
        demo = [
            ("Card payments failing for EU merchants", "incident", "urgent", "open", None, "Payments Ops"),
            ("Refund batch 7731 needs sign-off", "payment", "high", "awaiting_approval", "maya", "Payments Ops"),
            ("Settlement file for 18 Oct is missing", "payment", "high", "in_progress", "maya", "Payments Ops"),
            ("VIP customer charged twice", "customer_issue", "urgent", "open", None, "Customer Support"),
        ]
        for i, (title, item_type, priority, status, assignee_name, team_name) in enumerate(demo):
            created_at = now - timedelta(minutes=10 - i)
            assignee = user_ids[assignee_name] if assignee_name else None
            creator = user_ids["omar"]
            events = [dict(actor_id=creator, event_type="created", field=None, from_value=None,
                           to_value="open", created_at=created_at)]
            if assignee:
                events.append(dict(actor_id=assignee, event_type="changed", field="assignee",
                                   from_value=None, to_value=str(assignee), created_at=created_at))
            for step in range(STATUS_PATH.index(status)):
                events.append(dict(actor_id=assignee, event_type="changed", field="status",
                                   from_value=STATUS_PATH[step], to_value=STATUS_PATH[step + 1],
                                   created_at=created_at))
            items.append(dict(team_id=team_ids[team_name], title=title,
                              description="Demo item created by the seed script.", type=item_type,
                              priority=priority, status=status, assignee_id=assignee, created_by=creator,
                              due_date=(now + timedelta(days=1)).date(), version=len(events),
                              created_at=created_at, updated_at=created_at))
            histories.append(events)

        # Core table inserts (not ORM) so thousands of rows go to Postgres in a few big batches.
        items_table, activity_table = WorkItem.__table__, ActivityEvent.__table__
        new_ids = db.execute(
            insert(items_table).returning(items_table.c.id, sort_by_parameter_order=True), items
        ).scalars().all()
        activity = [dict(item_id=item_id, comment_id=None, **event)
                    for item_id, events in zip(new_ids, histories) for event in events]
        db.execute(insert(activity_table), activity)
        db.commit()
        db.execute(text("ANALYZE"))  # fresh statistics so the query planner picks the right indexes
        db.commit()
        print(f"Seeded {len(TEAMS)} teams, {len(USERS)} users, {len(items)} items, {len(activity)} activity events.")
        print(f"Every demo user's password is: {DEMO_PASSWORD}")


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
