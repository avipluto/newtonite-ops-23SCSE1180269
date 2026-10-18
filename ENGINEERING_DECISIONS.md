# Engineering decisions

This app is one tight vertical slice: work items with a server-enforced workflow, per-team roles,
an append-only history, a filtered dashboard and a reliable background worker. The brief rewards
"well-considered behaviour over feature count", so most of the effort went into what happens when
two people (or one impatient person) act at the same time.

Each decision below lists the **context**, the **options** I considered, the **choice**, and the
**trade-off** I accepted.

---

## 1. Data model and history: current-state row + append-only event log, written in one transaction

**Context.** Every important change (status, owner, priority and the other fields) must be recorded and
visible, and the history must never disagree with the item.

**Options.**
- *Event sourcing*: store only events and compute the item from them. Complete history, but every read
  has to rebuild state, and that is a lot of machinery for one day of work.
- *Database audit trigger* that copies every changed row into an audit table. Automatic, but it cannot
  see who made the change (the actor is an application concept) and it records rows, not intent.
- *Write the item first and the history afterwards* (separate calls). Simple, but if the second write
  fails, the history is missing a change.
- **Chosen:** keep the current state in `work_items`, and in the **same transaction** append one row to
  `activity_events` per changed field (`field`, `from_value`, `to_value`, `actor_id`).

**Choice details.**
- Service functions never commit. The route commits once at the end, so the item update, its activity
  rows and any outbox job are saved together or not at all (`backend/app/services/items.py`).
- A database trigger rejects `UPDATE` and `DELETE` on `activity_events`, so the log is append-only even
  if a bug or a person with SQL access tries to change it (migration `0001`, test
  `test_history_is_append_only_in_the_database`).
- Comments live in their own table and are linked from a `commented` event, so the timeline is one list.

**Trade-off.** State is stored twice (the row and the events), so it is not event sourcing: you cannot
rebuild an item from its events alone. The history is a log for humans, not the source of truth.

---

## 2. Concurrency: optimistic locking with a `version` column, and conditional updates

**Context.** Two people claim the same item, someone saves an edit made on a page that has gone stale,
and requests arrive close together.

**Options.**
- *Last write wins*: silently loses someone's change. Not acceptable for operations work.
- *Pessimistic locking* (`SELECT ... FOR UPDATE` while the user edits): locks would have to be held
  across HTTP requests while someone is typing, which does not work for a web app.
- *Application-level locks* (Redis, in-memory): an extra moving part, and wrong as soon as there are
  two API processes.
- **Chosen:** every write is a single conditional `UPDATE ... WHERE <guard> RETURNING *`:
  - edits and transitions: `WHERE id = :id AND version = :version_the_client_saw`, and `version + 1`.
  - claim: `WHERE id = :id AND assignee_id IS NULL AND status IN ('open','in_progress')`.

  If 0 rows are updated, someone else got there first, and the API returns **409** with the item's
  *current* state (`code: version_conflict` or `already_claimed`) so the UI can show what changed.

**Why it is correct.** Postgres checks the `WHERE` clause while it holds the row lock. When two claims
race, the second `UPDATE` waits for the first to commit, then re-checks `assignee_id IS NULL` against
the new row. It no longer matches, so exactly one claim succeeds. The test fires 6 claims in parallel and
asserts exactly one 200. I also removed the guard on purpose and checked that the test then fails (all 6 "won").

**Trade-off.** If two people edit *different* fields at the same time, the second still gets a conflict
and has to reload. A field-level merge would be friendlier, but harder to explain and to test.

---

## 3. Idempotency keys for create / claim / transition / comment

**Context.** A double-click or a network retry must not create two items or post two comments.

**Options.**
- *Only disable the button in the UI*: does not help when the network retries, or for API clients.
- *A unique constraint on business data* (e.g. title + team): blocks real duplicates that are wanted.
- *A cache like Redis*: one more service, and not transactional with the write.
- **Chosen:** the client sends an `Idempotency-Key` header (one random UUID per user action). The
  server stores `(user_id, key) → request fingerprint + response` in `idempotency_keys`, **in the same
  transaction** as the action (`backend/app/services/idempotency.py`).

**How the race is handled.** `INSERT ... ON CONFLICT DO NOTHING` on the key. If the same key arrives
twice at the same moment, Postgres makes the second insert wait until the first transaction finishes.
If the first committed, the second replays the stored response (header `Idempotent-Replayed: true`). If
it rolled back, the second runs the action itself. The same key used with a different body gets a 422.
Failed requests are not stored (they roll back), so a retry after an error really runs again.

**Trade-off.** One extra row write per request that sends a key, and keys are kept for 24 hours (the
worker cleans them up). Edits (`PATCH`) do not need keys because the version check already makes a
repeated edit fail safely.

---

## 4. Authorization: per-team roles, one check function, 404 for outsiders

**Context.** Permissions must be enforced on the server, per team and per resource.

**Options.**
- *Global roles* (one role per user): too coarse, since people are Leads in one team and Members in another.
- *A policy engine* (OPA, Casbin): powerful, but a lot to learn and explain for four roles.
- *Postgres Row-Level Security*: strong guarantee, but harder to test and debug in the time available.
- **Chosen:** `memberships(user_id, team_id, role)` with roles viewer < member < lead < admin, a single
  `MIN_ROLE` table, and one function `permissions.require(user, team_id, action)` that every service call
  goes through (`backend/app/permissions.py`). List queries always filter `team_id IN (my teams)`.

**Rules worth defending.**
- Someone outside the item's team gets **404**, not 403, so we don't reveal that the item exists.
  Someone inside the team with too low a role gets **403** with the reason.
- Approval is Lead/Admin only, and **you cannot approve your own work** (separation of duties). The seed
  data has two Leads in Payments Ops for this reason.
- Members can claim and work on items. Only Leads can assign or reassign other people.
- The API returns a `can` object on every item (which buttons to show). This is only a convenience
  for the UI; the server checks again on every request.

**Trade-off.** Roles are loaded on every request (one small query). There are no per-item ACLs (for
example "only the creator may edit"). RLS would be a good second layer later.

---

## 5. Sync vs async: transactional outbox in Postgres + a polling worker

**Context.** Notifications ("you were assigned", "waiting for your approval") must be reliable, but
sending them should not slow down or break the request.

**Options.**
- *Send inline in the request*: if the provider is slow or down, the user's action fails or hangs.
- *Push to Redis/BullMQ/Celery after commit*: if the process dies between the commit and the push,
  the notification is lost, and it adds a second piece of infrastructure.
- **Chosen:** a `jobs` table written **in the same transaction** as the change (the transactional outbox),
  and a worker process (`backend/app/worker.py`) that:
  - claims one job with `FOR UPDATE SKIP LOCKED`, so several workers never take the same job;
  - gives the claimed job a 60 s **lease** (`locked_until`), so if a worker crashes mid-job, another one
    picks the job up again later;
  - retries failures with exponential backoff (2 s, 4 s, 8 s, ...) up to `max_attempts` (5), then marks the
    job `failed` with `last_error`;
  - has an **idempotent handler**: `notifications` has `UNIQUE(job_id, user_id)` and inserts use
    `ON CONFLICT DO NOTHING`, so running a job twice still notifies each person once.

**Trade-off.** Delivery is *at-least-once* (hence the idempotent handler). Polling adds up to about 1 s of
latency and some load on Postgres. That is fine at this scale; at much larger scale I would move to a
dedicated queue (see "10× scale" below). "Delivery" is an in-app notification row, which stands in
for email, since email delivery was out of scope.

---

## 6. Search and pagination: keyset cursors + Postgres full-text search, filtered on the server

**Context.** Tens of thousands of items and a growing history, so we can't load everything into the browser.

**Options.**
- *OFFSET/LIMIT*: easy, but page 1,000 gets slow, and rows shift (skipped or repeated) when new items
  arrive while someone is paging.
- *Elasticsearch*: better search, but another system to run and keep in sync.
- *`ILIKE '%term%'`*: cannot use a normal index, so it scans every row.
- **Chosen:**
  - **Keyset pagination** ordered by `(created_at DESC, id DESC)`. The cursor is the last row's
    `(created_at, id)`, encoded, and the next page is `WHERE (created_at, id) < cursor`. The `id` breaks
    ties, so no row is ever skipped or repeated (tested).
  - **Full-text search** on a generated `tsvector` column (title + description) with a GIN index and
    `websearch_to_tsquery`, so "merchants" also matches "merchant".
  - Indexes chosen for the real queries: `(team_id, created_at DESC, id DESC)` for lists, `(assignee_id,
    status)` for "assigned to me", and *partial* indexes for "unassigned" and "awaiting approval".
  - The activity timeline is paginated the same way (`before=<event id>`).

**Trade-off.** No "page 7 of 200" or total count (counting is expensive at scale), one sort order, and
English-only stemming with no typo tolerance.

---

## 7. Frontend: small React + TanStack Query app, polling instead of websockets

- **TanStack Query** caches server data and refetches it when the window regains focus and on an
  interval (10–15 s). That keeps lists fresh without websockets, which the brief said to skip.
- **Optimistic UI** for claim and status changes: the page updates at once. If the server rejects the
  action (409/403), we put back the server's latest copy (or our snapshot) and show the server's message.
- **Edit form** remembers the version it started from. If polling brings in a newer version while you
  are typing, a warning appears before you press Save. If Save gets a 409, your edits are not applied
  and you are offered "Reload latest".
- FastAPI serves the built React files, so the reviewer only needs one URL and one port. The
  frontend is plain JavaScript (no TypeScript) to keep it small and easy to read.

---

## What I deliberately did not build

Email delivery (notifications are in-app), file attachments, SSO, sign-up, websockets, a mobile app,
fancy styling, moving items between teams, editing/deleting comments, and admin screens for managing
teams and memberships (they come from the seed script).

## Assumptions I would check with a product manager first

1. **Every item needs approval** before it is resolved, even a low-priority task. (Maybe only payment and
   compliance items should?) This is the one I'd check first: it adds a step to every item.
2. A Lead **cannot approve their own work**. Is that rule required (e.g. for audit), or just nice to have?
3. Any Member can edit any open item in their team, not only items they created or own.
4. `closed` is final; `resolved` can be reopened. Comments are still allowed on closed items.
5. Claiming is only allowed for unassigned items that are `open` or `in_progress`.
6. People outside a team must not even learn that an item exists (hence 404 rather than 403).

## What happens when things fail

| Failure | What happens |
|---|---|
| Two users claim at once | One gets 200; the others get 409 `already_claimed` with the winner shown. |
| Stale edit | 409 `version_conflict` with the current item. Nothing is overwritten. |
| Double-click / network retry | Same `Idempotency-Key` → the stored response is replayed, nothing runs twice. |
| Error halfway through a request | The single transaction rolls back: no half-written item, history or job. |
| Database down | The API returns 500 and the health check fails. The worker logs, waits 5 s and retries. Nothing is half-written. |
| Worker crashes mid-job | The job's lease expires and it is picked up again. The idempotent handler prevents duplicate notifications. |
| Notification handler keeps failing | Retries with backoff, then `failed` with `last_error` for a human to inspect. |

## What would change at 10× scale

- Read replicas for list and search queries. Writes stay on the primary.
- Partition `activity_events` (and `jobs`) by month; archive old history.
- Move search to OpenSearch/Elasticsearch, fed from the outbox.
- Replace the polling worker with a dedicated queue (SQS/RabbitMQ/Kafka), still fed by the outbox table.
- Push updates to browsers (websockets/SSE) instead of polling.
- Cache memberships per request/token, and add rate limiting per user.
