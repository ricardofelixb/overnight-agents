# Agents-app performance rules

Assess bounded Convex queries, index-backed pagination, subscriptions, client
rendering and native lifecycle costs, serial runtime/provider calls, and
avoidable startup work. Preserve reactive ordering, cancellation, voice,
Telegram, cron, and deployment semantics.

Rank duplicate model calls, unnecessarily repeated context, retries of billed
requests, and idle provider work as runtime cost findings. Count round trips
and measured work rather than treating fewer lines as proof of speed.

Keep platform-specific rendering in its client. Shared semantics may live in
packages only when stable and consumed by multiple clients. Preserve
visibility, reduced-motion, unsubscription, and native-resource cleanup.

Do not cache credentials, membership, or session material past its proven
invalidation boundary. Do not move one company's assumptions into core.
