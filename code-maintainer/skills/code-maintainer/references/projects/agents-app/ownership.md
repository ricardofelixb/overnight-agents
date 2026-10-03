# Agents-app ownership map

- `apps/web/` owns Next.js dashboard presentation and web auth adapters.
- `apps/desktop/` owns Electron main/preload boundaries, secure native sessions,
  IPC, native windows, and desktop presentation.
- `apps/mobile/` owns Expo navigation, native sessions, notifications, and
  platform presentation; iOS and Android share this one app.
- `packages/agent-protocol/` owns transport-neutral contracts.
  Design foundations, rich content, orb, and friends packages own shared
  semantics; platform adapters remain in their apps.
- `packages/zernio-*/` owns transport-injected module contracts and UI.
- `convex/` owns shared product persistence, tenant authorization, provisioning,
  conversation/voice contracts, schedules, and module business behavior.
- `runtime/core/` owns reusable execution, SDK/provider adapters, delivery,
  scheduling, tracing, and integrations.
- `runtime/companies/<company_slug>/` owns company config, prompts, business
  rules, source interpretation, workflows, and agent-specific tools/tests.
- `runtime/core/agent_templates/` owns reusable template behavior and
  conversational evaluations. Company correctness uses deterministic tests.
- `runtime/scripts/` and `runtime/infra/` own runtime tooling and VM services.
  Root `scripts/` and `infrastructure/` own product workspace/release tooling.

Follow `docs/architecture.md` and `runtime/docs/dashboard-integrations.md`
for cross-package work. Clients use authenticated contracts rather than
importing company packages or owning model execution. Before changing company
behavior, read that company's actual instructions and data-source contracts.
