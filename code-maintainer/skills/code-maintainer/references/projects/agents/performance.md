# Agents performance rules

Assess repeated process work, serial provider round trips, unbounded list or
report scans, and avoidable startup cost. Preserve Telegram, cron, voice, and
deploy lifecycle semantics.

Count network round trips and serial dependencies while preserving idempotency,
rate limits, retries, and provider error semantics. Never claim an optimization
from line count alone.

Do not cache credentials, membership, or session material beyond the
repository's proven invalidation boundary. Do not move company-specific
hot paths into core unless multiple companies share the same execution shape.
