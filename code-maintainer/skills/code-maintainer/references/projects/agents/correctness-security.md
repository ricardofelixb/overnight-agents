# Agents correctness and security rules

Every production-visible operation keeps Telegram webhook ownership, cron
execution, credentials, and company data on the intended tenant. Local
development must not steal production webhooks or write secrets into tracked
files.

Check:

- company isolation and config ownership;
- credential loading, ignored `.env` files, and secret leakage through logs or
  prompts;
- webhook authenticity, replay, and the split between production VM webhooks
  and local dry-runs;
- custom tools using `ToolDefinition` rather than provider-only schemas;
- deterministic Python ownership of business-critical calculations;
- deploy and cron scripts executing the intended company package;
- error messages that do not leak tokens, raw provider payloads, or other
  tenants' data.

Company and core regression tests live beside their owners. Cover success,
validation failure, and cross-company denial where the boundary exists. Do not
reimplement production logic in a test.

Do not expose internal jobs, management tokens, or storage-specific fields.
Security fixes preserve authorized behavior and must prove the denied path.
