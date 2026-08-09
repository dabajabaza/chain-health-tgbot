DEFAULT_CYCLE_LIMIT_KM = 300.0
INVITE_TTL_HOURS = 48
INVITE_CODE_BYTES = 16
MAX_DISTANCE_KM = 1000.0
# Cycle limit and resource_km are cumulative across many rides, not one ride's
# distance, so this is far more generous than MAX_DISTANCE_KM — it exists only
# to catch fat-finger input (a stray extra digit), not to bound anything real.
MAX_CYCLE_LIMIT_KM = 100_000.0
# Group/chain names have no DB-level length limit (SQLite doesn't enforce
# VARCHAR(N)), so this is enforced in the handler. Generous for any real name;
# tight enough that group_detail_text (name + limit + one line per chain)
# can never approach Telegram's 4096-char message limit.
MAX_NAME_LENGTH = 100
