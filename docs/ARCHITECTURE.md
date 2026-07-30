# Architecture decisions

Numbered, stable IDs (never renumbered) so code comments can reference them,
e.g. `# see ARCHITECTURE.md D9`. One file, not `docs/adr/NNNN-*.md`: for a
solo project `git log -p docs/ARCHITECTURE.md` gives the history for free,
and one file stays greppable in a single pass.

Each entry: **Context** (why this came up) → **Decision** → **Consequences**
→ **Revisit when** (the condition that would make us reconsider).

---

## D1 — SQLite only

**Context:** Single-user-ish personal bot on a home server; no multi-writer
concurrency, no need for a DB server process.

**Decision:** SQLite is the only supported backend. This is load-bearing in a
few places, not incidental:
- `Chain.__table_args__` uses a partial unique index (`sqlite_where=...`) to
  enforce "one active chain per group" — this is SQLite-dialect syntax.
- `User.current_group_id` has **no FK constraint**: it references
  `groups.id`, but `groups.user_id` also references `users.id`, closing a
  cycle. SQLite cannot `ALTER TABLE ADD CONSTRAINT` after creation, so a
  circular FK can't be resolved with `use_alter=True` the way other dialects
  allow. The constraint is app-enforced instead (see D15 below).
- `migrations/env.py` always configures `render_as_batch=True` — SQLite
  can't do most `ALTER TABLE` operations directly; Alembic's batch mode
  recreates the table under the hood for every column add/rename/drop.
- The partial unique index also means `GarageService.add_chain`/`rotate` must
  deactivate the previously-active chain in its **own** `flush()`, before
  activating the new one. SQLAlchemy batches same-table UPDATEs by primary
  key order, not by the order attributes were set in Python — a single flush
  covering both rows can transiently activate two chains at once and trip
  the index, depending on which row's id sorts first.

**Consequences:** Moving to Postgres is a project, not a config change —
budget for revisiting the partial index and the FK.

**Revisit when:** multiple concurrent writers are needed, or the DB needs to
live on a different host than the bot process.

## D2 — Migrations run at startup

**Context:** One process, no separate deploy step, no ops team.

**Decision:** `__main__.main()` runs `alembic upgrade head` synchronously
before starting the bot. A restart is therefore the normal, expected way a
schema change reaches production.

**Consequences:** No manual migration step to forget. But it means every
migration must be safe to run against a live (if idle) SQLite file with no
rollback window beyond `alembic downgrade`, and it means FSM state (D5) is
always expected to be lost on every deploy.

**Revisit when:** migrations start taking long enough to matter, or there are
multiple bot instances that would race on `upgrade head`.

## D3 — Router registration order carries meaning

**Context:** aiogram matches handlers in registration order within a router,
and outer-middleware chains run in registration order too.

**Decision:** `chain_health.__main__.build_dispatcher` fixes one order:
`PrivateChatOnlyMiddleware` (before `setup_dishka`, so no dishka scope opens
for group traffic) → routers (`admin, start, status, menu, rotation, rides,
mileage, fallback`) → `dp.errors.register` → `setup_dishka` → `AuthMiddleware`.
Only one thing actually depends on this order: `fallback.router` must be
**last** — its `@router.callback_query()` has no filter at all and catches
anything nothing else matched. (`mileage.router`'s loose numeric regex
(`NUMERIC_MESSAGE_RE`) is guarded by `StateFilter(None)`, so a bare number
typed mid-dialog never reaches it regardless of registration order — the
state filter alone rules it out, not position in the list.)

**Consequences:** Adding a new router means picking a position, not just
appending — specifically, before `fallback`. `build_dispatcher` is the single
place production and the test harness both call, so the two can never
diverge (see D16).

**Revisit when:** the routing surface grows enough that "must come last"
constraints multiply past what a linear list can express clearly.

## D4 — The bot stays silent to strangers

**Context:** Invite-only personal bot; no interest in a spam surface or an
access-request queue to moderate.

**Decision:** `AuthMiddleware` returns `None` for anyone not admin/allowlisted
and without a valid invite code — no reply, no error, nothing. The only ways
in are a one-time `/invite` deep link or an admin's `/allow <id>`.

**Consequences:** A misconfigured bot token or broken deploy looks identical
to "the bot is ignoring me" from a stranger's perspective — there is
deliberately no user-facing signal to distinguish them. Operator-facing
signal exists instead: denials are logged (WARNING on first sight of a
user/chat id, DEBUG after, so a spam bot can't flood the log).

**Revisit when:** the user base grows past "people I personally invited".

## D5 — FSM state lives in memory only

**Context:** `Dispatcher()` uses aiogram's default `MemoryStorage`.

**Decision:** No Redis, no DB-backed FSM storage. Combined with D2, this
means **every deploy silently drops anyone mid-dialog** (onboarding, editing
a limit, editing a ride amount, ...).

**Consequences:** Every FSM flow must be safely re-enterable from the menu —
there is no "resume where I left off" and there must never need to be one.
Handlers that read FSM-stored ids (e.g. `data["chain_id"]`) always
re-authorize via `garage.require_chain(...)` rather than trusting the stored
value, which also protects against the state having been populated from an
untrusted callback payload in the first place (see D15).

**Revisit when:** dialogs become long/valuable enough that losing one to a
routine restart would be a real cost.

## D6 — Counters are derived on read

**Context:** `cycle_km`/`total_km` are computed by summing `rides` on every
read, not maintained as a running total on `Chain`.

**Decision:** No denormalized counters. `StatusService.build` for one user
does roughly 3N+3 queries (N = chains in the current group) — two for
`current_group` (`session.get(User)` then the `Group` lookup), one for
`list_chains`, then per chain: cycle sum, total sum, plus the rotation
lookup inside `cycle_km`.

**Consequences:** Correctness by construction (a counter can never drift from
the ledger it's supposed to summarize) at the cost of query count. Fine at
the current scale (single local SQLite file, a handful of chains per
group). `MileageService.record_ride` recomputes a `ChainStatus` and
`Responder.reply` immediately recomputes the *whole* `StatusView` again for
the placeholder/pinned sync — a deliberate small duplication rather than
threading a precomputed view through, because the two call sites shouldn't
have to agree on shape. `ReminderService.due_user_ids` similarly builds a
full `DueReminder` (with its `ChainStatus`) just to project out the user id,
and `scheduler.py`'s phase 2 recomputes the same status per user again in a
fresh scope — that one is *not* the same trade-off as the two above (see the
comment on `due_user_ids`): it exists because phase 2 must re-verify against
current data in its own transaction, not because reuse was deemed not worth
threading through.

**Revisit when:** a status build shows up as slow, or a chain accumulates
enough rides that summing them becomes measurable.

## D7 — Button text doubles as a routing key

**Context:** `F.text == texts.BTN_STATUS` / `F.text == texts.BTN_MENU` are
literal-string filters matching the reply-keyboard button labels.

**Decision:** Accepted as-is, but mitigated: `/status` and `/menu` are
registered as command aliases (`or_f(Command("status"), F.text == ...)`) and
advertised via `bot.set_my_commands` at startup. Renaming a button label
would otherwise silently strand anyone still holding the old
`ReplyKeyboardMarkup` (Telegram doesn't force-refresh a client's keyboard) —
the command aliases make the bot reachable regardless of which keyboard
generation a given chat is showing.

**Consequences:** Any *new* button text still needs a command alias if it's a
primary entry point, or a rename risks the same trap. Inline-keyboard actions
(`CallbackData`-based) don't have this problem — those route on structured
`callback_data`, not display text, and old inline buttons in chat history
keep working as long as the `CallbackData` schema is unchanged (see D15 on
payload schema stability).

**Revisit when:** never renaming `BTN_STATUS`/`BTN_MENU` without also keeping
(or aliasing) the old string for one release.

## D8 — Private chats only

**Context:** Every handler and the reminder notifier assume `chat_id ==
user_id`. In a group chat, an inline button the bot sent to user A is
visible and pressable by user B — before this decision, that was a live way
to reach another user's data with a stock Telegram client, no forged payload
needed.

**Decision:** `PrivateChatOnlyMiddleware`, registered on
`dp.update.outer_middleware` **before** `setup_dishka`, drops any update
whose `event_chat.type != ChatType.PRIVATE` — deny by default, including when
chat type can't be determined at all. It runs before any dishka container or
DB session opens for the update, so group traffic never reaches
`AuthMiddleware.ensure_registered` either.

**Consequences:** `chat_id == user_id` is a safe invariant everywhere else in
the codebase (`TelegramReminderNotifier.send_reminder` relies on it directly).
Group chats get total silence, same as strangers (D4). Worth disabling group
invites for the bot in BotFather (`/setjoingroups` → Disable) so the traffic
never arrives in the first place, not just gets dropped.

**Revisit when:** a group-chat use case is actually wanted — this would need
per-chat state (not per-user) and a real authorization model, not a config
flip.

## D9 — Cycle boundary: composite date comparison

**Context:** A chain's "current cycle" starts at its last rotation. Rides can
be entered for a past date (already true for manual "forgot to log
yesterday's ride"; will be the *default* case once stage-2 importers exist).
The earlier design compared only `Ride.created_at >= Rotation.activated_at`
(insertion instant vs. activation instant) — a ride entered today for
yesterday's date would incorrectly land in *today's* cycle if rotated today,
and a stage-2 import would misattribute historical rides to whatever cycle
happens to be current at import time.

**Decision:** Two clocks, never mixed:
- **Local calendar date** — `Ride.ride_dt`, `Rotation.activated_dt` (naming:
  see Naming Conventions below). "Did this ride happen before or after the
  day the chain became active?"
- **UTC instant** — `Ride.created_at`, `Rotation.activated_at`. Tie-breaks
  same-day: "was this ride recorded before or after the exact moment of
  activation?"

The predicate (`services/rides.py::_cycle_predicate`):
```python
or_(
    Ride.ride_dt > boundary.activated_dt,
    and_(Ride.ride_dt == boundary.activated_dt, Ride.created_at >= boundary.activated_at),
)
```
`GarageService._activate` computes both halves from **one** `utcnow()` call
(`activated_at`, and `activated_dt = to_local_date(activated_at, tz)`) —
deriving `activated_dt` from a second, separate "now" call would open a
one-in-a-billion midnight-skew window where the two halves disagree.

**Consequences:** Correct handling of backdated rides and same-day
re-rotation (A → B → A, all on one calendar day) without needing wall-clock
precision on the "which day" question. Requires `Settings` (for the
configured timezone) injected into `GarageService`.

**Revisit when:** never, unless multi-timezone users are supported (right
now there's one `TZ` in `.env` for the whole bot).

## D10 — Side effects before commit

**Context:** Telegram sends happen inside the request's dishka scope,
*before* the session commits (there's no two-phase commit between "send a
message" and "write a row").

**Decision:** Deliberately asymmetric:
- The **primary reply** and the transaction live and die together — if
  `send_message` fails, letting the transaction roll back is *correct*:
  nothing was recorded, the user's retry doesn't create a duplicate.
- The **pinned status message** is best-effort. `Responder._sync_pinned`
  catches `TelegramAPIError` around the `pin_chat_message` call specifically
  and logs a warning rather than raising — a pin failure must never discard
  an already-recorded ride. It also stores the pinned message id **before**
  attempting to pin, so a pin failure doesn't orphan the message (the next
  sync would otherwise create a duplicate one, forever).

**Consequences:** Nothing structural was added to fix this fully (it can't be
fixed fully without 2PC with Telegram, which doesn't exist). The residual
hazard: a *commit* failure (not a send failure) surfaces as
`dishka.exceptions.ExitError` raised from the request scope's `__aexit__`,
**after** the handler already returned and the reply was already sent — on a
local SQLite file this means disk-full or corruption, not a normal failure
mode.

**Revisit when:** moving off SQLite to something where commit failures are a
realistic operational event.

## D11 — dishka finalizes generators with `asend`, not `athrow`

**Context:** Found while fixing the request-scope unit-of-work. dishka's
`AsyncContainer.__aexit__` finalizes a generator-based provider with
`await agen.asend(exception)` — the exception object is **sent in as the
value the `yield` expression evaluates to**, not raised inside the generator.
A `try: yield session; commit() / except Exception: rollback()` therefore
never observes a failure: the `except` branch is unreachable dead code, and
`commit()` runs unconditionally, even for a handler that raised.

**Decision:** `RequestProvider.session` reads the yielded value instead of
wrapping it in `try/except`:
```python
exception = yield session
if exception is None:
    await session.commit()
else:
    await session.rollback()
```

**Consequences:** This was silently committing every failed handler's
partial writes before the fix (verified: a handler that flushed a write then
raised left the write persisted). `tests/test_di.py`'s
`test_request_scope_rolls_back_on_exception` pins this down with two
**independent, sequential** scopes (each gets its own session — see
`test_two_request_scopes_get_independent_sessions`): the first adds a row,
flushes it, then raises; the second, opened fresh afterward, queries for
that row and asserts it's absent. A future "helpful" refactor back to
`try/except` would make that row survive, and the test would catch it
immediately.

**Revisit when:** upgrading dishka major versions — re-verify this is still
the finalization protocol before touching this provider.

## D12 — ORM entities double as view models

**Context:** `domain/values.py` (`ChainStatus`, `StatusView`, ...) holds
plain dataclasses, but `ChainStatus.chain` is a live `db.models.Chain`
instance, and `bot/texts.py` / `bot/keyboards.py` read attributes off `Chain`,
`Group`, `Ride` directly (`chain.is_active`, `ride.ride_dt`, ...).

**Decision:** No DTO/view-model layer between the ORM and the presentation
layer. `domain/` holds types (dataclasses, enums, errors, ports) that are
free of SQLAlchemy *behavior*, but `domain/values.py` imports `db.models` for
the entity types themselves — a deliberate, one-directional dependency
(`domain → db`, never the reverse).

**Consequences:** Cheap and direct for a two-screen personal bot. The cost:
`bot/` is coupled to ORM attribute names, and every eager-load matters —
`services/rides.py::recent_rides` uses `selectinload(Ride.chain)` specifically
because `keyboards.py::rides_list_keyboard` reads `ride.chain.name` outside
any session-bound lazy-load context.

**Revisit when:** the UI surface grows enough that "what a screen needs" and
"what a table has" diverge meaningfully — that's the point a real view-model
layer earns its cost.

## D13 — Stage 2 (external mileage sources): open questions

**Context:** The plumbing for stage-2 import sources already exists —
`domain/ports.py::MileageSource`, `domain/values.py::ExternalRide`,
`domain/enums.py::RideSource`, and `source`/`external_id` parameters threaded
through `RideService.add_ride`/`MileageService.record_ride` — but no adapter,
token storage, or cursor persistence is built on top of it.

**Decision:** Keep the port honest about what it doesn't solve rather than
build speculative machinery:
- `fetch_rides(user_id, since: date | None)` — the caller (not the adapter)
  owns the cursor; there's nowhere yet to *persist* that cursor between runs.
- No OAuth/token entity anywhere in `db/models.py`. Token storage (encryption
  at rest, refresh flow, revocation) is unbuilt.
- Dedup relies on `UniqueConstraint("source", "external_id")` on `Ride`, but
  `RideService.add_ride` doesn't catch `IntegrityError` — nothing calls it
  with a real `external_id` yet, so this is a latent gap, not an active bug.
- `AppProvider.mileage_sources` returns `{}` — the registered extension
  point, deliberately empty.

**Consequences:** The first real adapter (Strava, most likely) will need a
schema migration for at least: a per-user credential/token table, a cursor
column (or table), and an idempotent-insert path in `RideService`. Don't
treat the current port as more finished than it is.

**Revisit when:** starting the first stage-2 adapter — resolve cursor
persistence and token storage as their own design pass before writing
adapter code.

## D14 — Rejected as over-engineering at this scale

- **Repository layer over `AsyncSession`.** Services already are the
  narrow interface; a repository would just rename the same methods.
- **CQRS / event sourcing.** No read/write split need, no audit-log
  requirement beyond what INFO-level logging already gives.
- **i18n framework.** One language (Russian), `bot/texts.py` is a plain
  module of strings/formatters. Add a framework if a second language is ever
  actually needed.
- **Separate scheduler process.** `run_reminder_scheduler` is one
  `asyncio.create_task` inside the same process as the bot — a personal
  bot's reminder volume doesn't justify a second deployable.
- **Postgres "just in case."** See D1 — SQLite is right for this scale;
  premature portability would mean designing around constraints (concurrent
  writers, network partitions) that don't exist yet.
- **A `Clock` protocol / DI-injected time source.** `GarageService` takes
  `Settings` directly and calls `timeutils.utcnow()` — a full clock
  abstraction is the textbook answer for testability, but tests here just
  call the real (fast, deterministic-enough) functions; not worth the
  indirection for a single-timezone personal bot. `scheduler.py`'s timing
  functions do the same — no `Protocol`, no injectable "now" parameter; tests
  monkeypatch `chain_health.timeutils.utcnow` (the one clock read every
  timing function goes through via `timeutils.local_now`) rather than the
  production code growing a seam for it.

## D15 — Ownership authorization: scoped resolvers, not a check per call site

**Context:** Every mutating operation (rename a chain, change a limit, rotate,
delete a ride, ...) is reachable from a callback payload or FSM-stored id
that didn't necessarily originate from this user — a payload copied from
another chat, or a stale one from before a chain was reassigned. Putting
"does this belong to `user_id`?" as a check inside each of those ~13 method
bodies means each one has to remember to call it.

**Decision:** Scoped resolvers are the *only* way to turn an untrusted id
into an entity: `GarageService.require_group(user_id, group_id) -> Group`,
`require_chain(user_id, chain_id) -> Chain`, `RideService.require_ride(user_id,
ride_id) -> Ride`. Every one of them raises the same `NotFoundError` whether
the id doesn't exist at all or exists but belongs to someone else — there is
no oracle that lets a caller distinguish "not found" from "not yours" by
trying both. Every mutating operation then takes the **entity**, not a bare
id (`rotate(chain: Chain)`, `set_chain_limit(chain: Chain, ...)`, ...) — a
handler that skips `require_chain` has no id to pass in the first place, so
the type signature itself makes the unsafe path harder to reach than the
safe one. `menu.py`'s `_group_detail_view`/`_chain_detail_view` are the sole
authorization point for the detail screens; FSM handlers that resume with a
stored id (see D5) re-run the resolver at the point they use it rather than
trusting what's in state, since the state was itself populated from a
callback payload.

`current_group(user_id)` additionally filters by `Group.user_id` (not just
`Group.id == user.current_group_id`) — a second line of defense in case
`current_group_id` were ever poisoned by a caller that skipped
`set_current_group`'s own `require_group` check.

**Consequences:** Adding a new mutating operation on an existing entity type
is free (it just takes the entity); adding a new entity type means writing
one `require_X`. The convention is enforced by code review and the
`test_require_*`/`test_current_group_ignores_a_poisoned_*` tests in
`test_garage.py`/`test_rides.py`, not by the type system — nothing stops a
new handler from calling `session.get(Chain, id)` directly and bypassing it.

**Revisit when:** never, unless a second "owner" concept is introduced (e.g.
shared groups) — the current model assumes exactly one owning user per
entity.

## D16 — Test strategy

**Context:** Tests need the production schema (not an approximation that can
silently drift — this happened once early on, before migrations were
squashed into one; see commit `3ca9e24`) and need to drive real
handlers/middleware through the real `Dispatcher`, not a reimplementation of
the routing.

**Decision:**
- `tests/schema.py::apply_migrations` runs the real Alembic chain against a
  sync SQLite engine once per test session (`conftest.py::migrated_template`);
  each test gets a private `shutil.copyfile` of that template
  (`conftest.py::db_path`) rather than re-running migrations per test or
  falling back to `Base.metadata.create_all`, which is exactly the drift
  `test_schema.py::test_metadata_matches_migrations` (`compare_metadata`) is
  there to catch.
- `tests/bot_harness.py::BotHarness` drives `chain_health.__main__
  .build_dispatcher` — the *same* function production calls — through
  fabricated `Update`s, with `RecordingSession(BaseSession)` standing in for
  the network. This is what makes D3's ordering claims checkable at all:
  production and the test harness share one dispatcher-construction path,
  so they cannot drift apart.
- aiogram's `Router` can only ever attach to one parent `Dispatcher` for its
  lifetime, but the handler modules' routers are module-level singletons (as
  they must be in production — a real process builds its dispatcher exactly
  once). `conftest.py::harness`'s teardown resets each shared router's
  `_parent_router` to `None` so the next test's fresh `Dispatcher` can attach
  them again.

**Consequences:** A schema-drift bug or a routing-order bug shows up as a
test failure, not a runtime surprise. The cost is a small amount of
per-test setup (one file copy, one dispatcher rebuild) — fast enough at this
scale not to matter (152 tests in a few seconds).

**Revisit when:** the test suite's wall-clock time becomes a problem, or a
second bot instance/process needs to share the router singletons safely.

## D17 — Scheduler stays a pure timing module

**Context:** `scheduler.py` decides *when* to run a reminder pass; it must
never decide *what* a reminder says or *how* it's delivered, or the
scheduler/presentation coupling this codebase deliberately removed (see the
architectural remediation this doc's D1–D14 came out of) creeps back in.

**Decision:** `scheduler.py` imports neither `bot.texts` nor anything from
`bot/` — `ReminderService`/`ReminderNotifier` (see D4, D8) own the "what" and
"how". `test_scheduler.py::test_scheduler_module_does_not_import_the
_presentation_layer` is a fitness test asserting `"texts"`/`"Responder"` are
absent from `vars(scheduler_module)`; it fails immediately if a future change
reaches back into the presentation layer from here.

**Consequences:** Cheap to keep once established; the fitness test is the
guard against silent regrowth of the coupling, not code review alone.

**Revisit when:** never — if scheduler.py ever needs bot/texts, that's a sign
the decoupling itself needs revisiting, not just this test.

## D18 — Resource-based wear warning (`resource_km`)

**Context:** The cycle limit (`cycle_limit_km`) tracks when to rotate to a
spare chain, but a chain's own physical wear (stretch, measurable with a
chain-wear calibrator) is a separate concern from how many cycles it's been
through — a chain can wear out from lifetime mileage even if it's never
exceeded a single cycle's limit.

**Decision:** `Chain.resource_km` is an optional, independent lifetime-km
threshold. `ChainStatus.resource_warning` is true once `total_km` reaches it,
shown as a "⚠️ проверь износ калибром" marker in chain listings
(`bot/texts.py::_chain_line`) and folded into `ReminderService.due_reminders`
alongside `over_limit` — either condition makes a user's active chain due for
a reminder. Not exposed as a per-cycle thing because chain wear doesn't reset
on rotation the way a cycle's mileage does.

**Consequences:** A chain can be simultaneously "not due to rotate yet"
(`over_limit=False`) and "due a reminder anyway" (`resource_warning=True`) —
intentional, since the two questions ("is this cycle over?" and "is this
physical chain worn?") are independent.

**Revisit when:** never, unless resource-based wear needs its own dedicated
reminder cadence separate from the daily cycle-limit check.

## D19 — Startup catch-up reminder run

**Context:** `run_reminder_scheduler` sleeps until `REMINDER_TIME` and runs a
pass, then sleeps 24h and repeats (see D2 on restarts being routine). A naive
"sleep until next occurrence" would silently skip today's reminder entirely
if the process starts *after* `REMINDER_TIME` on a given day — e.g. a restart
at 20:00 with `REMINDER_TIME=19:00` would wait until tomorrow's 19:00.

**Decision:** `run_reminder_scheduler` checks `is_past_reminder_time` once at
startup; if true, it runs a reminder pass immediately before entering the
sleep loop. `send_due_reminder`'s notify-then-mark ordering (D10-adjacent:
side effects before the state that would suppress a repeat) makes this safe
to run redundantly — a user already reminded today is simply not due,
regardless of how many times the check runs.

**Consequences:** A restart at any time of day still reminds anyone who was
due and hadn't been reminded yet, at the cost of the reminder landing at
whatever time the process happened to restart rather than exactly
`REMINDER_TIME`, for that one day.

**Revisit when:** exact reminder timing becomes a requirement (it currently
isn't — "sometime after the configured time" is the actual guarantee).

## D20 — Validation and moderation limits

**Context:** A handful of numeric limits exist purely to reject obviously-bad
input or bound unbounded growth, without a natural home in a single service.

**Decision:**
- `domain.constants.MAX_DISTANCE_KM = 1000.0` rejects both zero (`<=0` in
  `parse_positive_float`) and implausible values (a pasted odometer reading,
  a misplaced decimal) — 600km (a brevet) is a real ride; 1000km is chosen to
  clear that with margin while still catching a stray extra digit.
- `GarageService.rotation_options`'s recommendation ties go to whichever
  candidate chain is oldest (`min()`'s stable tie-break over `list_chains`'s
  `created_at`-ordered result) — arbitrary but deterministic, and "oldest
  spare first" is a reasonable default absent any other signal.
- `bot/middlewares.py::_DenialLog` bounds its seen-id set to 256 entries,
  clearing it wholesale on overflow — accepts a burst of repeat WARNINGs
  over unbounded memory growth from a spam bot hammering `AuthMiddleware`.

**Consequences:** None of these are load-bearing business rules — moving any
of them requires no migration or compatibility concern, just picking a new
constant.

**Revisit when:** real usage data suggests a different cutoff (e.g. an
actual ultra-distance rider hitting `MAX_DISTANCE_KM`).

## D21 — In-process concurrent updates, not just multi-writer concurrency

**Context:** D1 rules out concurrent *writer processes* — but aiogram still
runs each incoming update as its own concurrent `asyncio` task within the
single bot process, each opening its own dishka-scoped session. Two updates
from the same user (e.g. a double-tapped button, or their very first message
arriving as two near-simultaneous updates) can interleave inside one process
exactly like two separate writers would.

**Decision:** Two different tools for two different race shapes, both in
`AccessService`:
- `ensure_registered`'s insert-or-get race (two concurrent first-contacts
  both seeing `user is None`) is handled with a `begin_nested()` SAVEPOINT
  around the insert: a `UNIQUE`-constraint loss rolls back only the insert,
  and the loser re-fetches the row the winner committed.
- `redeem_invite`'s one-time-claim race (two concurrent redemptions of the
  same code) is handled with a single atomic `UPDATE ... WHERE used_by IS
  NULL`, re-checked at write time — SQLite serializes writers, so only one
  `UPDATE` can match the row and get `rowcount == 1`.

**Consequences:** A plain read-then-write (`get`, check, then `add`/`update`)
is not safe for either case, even on SQLite, even single-process — the
`await` points in between are exactly where the second task's turn runs.
Any future "claim a row exactly once" or "insert unless it already exists"
operation needs the same treatment, not a read-then-write.

**Revisit when:** a new mutating flow needs "exactly once under concurrent
callers" semantics — check whether it's an insert-or-get or a claim, and
reuse the matching pattern above rather than inventing a third one.

---

## Naming conventions

**Date/time columns:** `_at` suffix = second-or-finer precision, stored via
`db.types.UTCDateTime` (see below); `_dt` suffix = calendar date only, no
time component, stored via plain SQLAlchemy `Date`. E.g. `Ride.created_at`
(instant) vs. `Ride.ride_dt` (which day the ride happened).

**Timezone-aware storage:** every `_at` column is timezone-aware in Python
(`timeutils.utcnow()` returns an aware UTC `datetime`; `db/types.py::UTCDateTime`
enforces aware-in, aware-out). SQLite itself has no native tz-aware timestamp
type — verified empirically that plain SQLAlchemy `DateTime(timezone=True)`
silently drops the offset on this dialect — so `UTCDateTime` is a
`TypeDecorator` that normalizes to UTC before storing (as the same naive
string a plain `DateTime` would use) and reattaches `tzinfo=UTC` on read.
This means every `datetime` object flowing through the application is
unambiguous and comparable without a class of naive/aware mixing bugs, even
though the on-disk bytes are unchanged from a naive column.

**Migration filenames:** `alembic.ini`'s `file_template` prefixes every
migration with a human-readable `YYYY-mm-DD_HH-MM-SS` timestamp, so files
sort chronologically on disk without needing to open them. Alembic resolves
migration order via the `revision`/`down_revision` chain inside each file,
never the filename — renaming a file (as was done when this convention was
introduced) is always safe.
