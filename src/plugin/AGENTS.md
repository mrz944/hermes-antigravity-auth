# src/plugin — TypeScript Plugin Core

The `@opencode-ai/plugin` implementation: fetch interception, request transformation, auth, quota, and multi-account rotation for OpenCode.

## Module Map

```
src/plugin/
├── plugin.ts                # Main entry — registers provider, hooks, lifecycle
├── auth.ts                  # Token validation + refresh (exported as OAuthAuthDetails)
├── request.ts               # Core transform: messages → Antigravity format, thinking, warmup
├── request-helpers.ts       # Schema cleaning (const→enum, strip $ref/$defs), thinking strip
├── thinking-recovery.ts     # Turn-boundary detection, thought signature management
├── recovery.ts              # Session recovery: tool_result_missing injection
├── quota.ts                 # Quota checking, usage stats, refresh
├── cache.ts                 # In-memory + disk signature cache
├── accounts.ts              # Multi-account load balancing, rotation strategies
├── storage.ts               # Zod schemas + persistent account storage
├── fingerprint.ts           # Device fingerprint generation
├── project.ts               # Managed project context resolution (loadCodeAssist)
├── debug.ts                 # Debug logging utilities
├── refresh-queue.ts         # Concurrent token refresh deduplication
├── rotation.ts              # Strategy-based account rotation (hybrid/sticky/round-robin)
├── errors.ts                # Custom error classes
├── image-saver.ts           # Base64 image extraction from responses
├── logging-utils.ts         # Logging helpers
├── cli.ts                   # CLI auth interaction flow (login, verify, config models)
├── config/                  # Plugin configuration
│   ├── schema.ts            # Zod config schema + defaults
│   ├── loader.ts            # Config file loading
│   └── updater.ts           # Live config updates
├── transform/               # Request/response transformation sub-modules
│   ├── gemini.ts            # Gemini-specific transforms (googleSearchRetrieval)
│   ├── claude.ts            # Claude-specific transforms
│   ├── model-resolver.ts    # Model name normalization across header styles
│   └── cross-model-sanitizer.ts  # Shared schema sanitization + tool hardening
├── ui/                      # Interactive TUI menus
│   ├── auth-menu.ts         # Account selection/management menu
│   ├── select.ts            # Generic selection prompt
│   ├── confirm.ts           # Confirm prompt
│   └── ansi.ts              # ANSI color/style utilities
├── stores/                  # Plugin state stores
│   └── (session state, auth state)
├── core/streaming/          # SSE streaming transformer
│   ├── transformer.ts       # Streamed payload transformation
│   ├── types.ts             # Streaming type definitions
│   └── index.ts             # Streaming module entry
└── recovery/                # Detailed recovery sub-modules
    └── (specialized recovery handlers)
```

## Key Conventions

- `strict: true` TS config with `noUncheckedIndexedAccess`, `noImplicitOverride`
- `verbatimModuleSyntax`: use `import type` for type-only imports
- Named imports + exports only — no default exports in src/
- `.ts` extensions in all relative imports
- `export function` for public APIs, arrow functions for callbacks
- Discriminated unions > boolean flags
- No `as any`, `@ts-ignore`, or `@ts-expect-error`
- Tests colocated: `foo.test.ts` next to `foo.ts`

## Testing

```bash
npx vitest run src/plugin/       # All plugin tests
npx vitest run -t "test name"    # Single test
npm run test:coverage            # Coverage report
```

## Port Status

This is the source for the Python port at `antigravity_auth/`. Core modules (auth, transform, recovery, quota, accounts) are ported. Streaming transformer, refresh queue, and disk signature cache are not yet ported.
