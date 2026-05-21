# AGENTS.md

Guidance for AI agents working with this repository.

## Overview

OpenCode plugin for Google Antigravity OAuth. Intercepts `fetch()` calls to `generativelanguage.googleapis.com`, transforms them to Antigravity format, and handles auth, quota, recovery, and multi-account rotation.

## Build & Test Commands

```bash
npm install                          # Install dependencies
npm run build                        # Compile (tsc -p tsconfig.build.json)
npm run typecheck                    # Type-check only (tsc --noEmit)
npm test                             # Run all tests (vitest run)
npx vitest run src/plugin/auth.test.ts          # Single test file
npx vitest run -t "test name here"              # Single test by name
npx vitest --watch src/plugin/auth.test.ts      # Watch mode, single file
npm run test:coverage                # Coverage report
npm run test:e2e:models              # E2E: model availability check
npm run test:e2e:regression          # E2E: regression suite
```

No linter or formatter is configured. Style is enforced by convention (see below).

## TypeScript Configuration

- `strict: true` with extra strictness: `noUncheckedIndexedAccess`, `noImplicitOverride`, `noFallthroughCasesInSwitch`
- `verbatimModuleSyntax: true` — use `import type` for type-only imports
- `target: ESNext`, `module: Preserve`, `moduleResolution: bundler`
- `allowImportingTsExtensions: true` — use `.ts` extensions in imports
- No path aliases — all imports are relative

## Code Style

### Imports
- Use `import type { ... }` for type-only imports (enforced by `verbatimModuleSyntax`)
- Named imports only — no default imports in src/
- Relative paths with `.ts` extensions: `import { foo } from "./bar.ts"`
- Order: node builtins > external packages > local modules

### Exports
- Named exports only in src/ — no default exports
- Barrel files (index.ts) for module surfaces

### Naming
- `camelCase` for functions, variables, parameters
- `PascalCase` for types, interfaces, classes, enums
- `UPPER_SNAKE_CASE` for constants
- `kebab-case` for file names (e.g., `request-helpers.ts`, `thinking-recovery.ts`)
- Test files: `*.test.ts` colocated with source

### Types
- No `I` prefix on interfaces, no `Type` suffix
- Use `z.infer<typeof Schema>` for Zod-derived types
- Extract to `types.ts` when shared, inline when local
- Discriminated unions preferred over boolean flags
- Never use `as any`, `@ts-ignore`, or `@ts-expect-error`

### Functions
- `export function` for public APIs
- Arrow functions for callbacks, factories, and inline closures
- Async functions with targeted try/catch (not blanket)

### Error Handling
- Defensive try/catch with graceful degradation (fallback values, not crashes)
- Custom error classes with metadata when domain-specific
- Catch `unknown`, log, and convert to domain errors — never empty catch blocks
- Rate limit / quota errors trigger account rotation, not failure

### Formatting
- 2-space indentation
- Double quotes for strings
- Trailing commas in multiline constructs
- No semicolons (project convention)

### Logging
- `createLogger("module-name")` for structured logging
- `console.log` only for CLI/user-facing output

## Module Structure

```
src/
├── plugin.ts                # Main entry, fetch interceptor
├── constants.ts             # Endpoints, headers, API config, system prompts
├── antigravity/oauth.ts     # OAuth token exchange
└── plugin/
    ├── auth.ts              # Token validation & refresh
    ├── request.ts           # Request transformation (core logic)
    ├── request-helpers.ts   # Schema cleaning, thinking filters
    ├── thinking-recovery.ts # Turn boundary detection
    ├── recovery.ts          # Session recovery (tool_result_missing)
    ├── quota.ts             # Quota checking (API usage stats)
    ├── cache.ts             # Auth & signature caching
    ├── accounts.ts          # Multi-account management & storage
    ├── storage.ts           # Persistent storage schemas (Zod)
    ├── fingerprint.ts       # Device fingerprint generation & headers
    ├── project.ts           # Managed project context resolution
    └── debug.ts             # Debug logging utilities
```

## Key Design Patterns

### 1. Request Interception
Plugin intercepts `fetch()` for `generativelanguage.googleapis.com`, transforms to Antigravity format. Two header styles: `antigravity` (Electron-style UA + fingerprint) and `gemini-cli` (nodejs-client UA).

### 2. Claude Thinking Blocks
ALL thinking blocks are stripped from outgoing requests for Claude models. Claude generates fresh thinking each turn. This eliminates signature validation errors.

### 3. Session Recovery
When tool execution is interrupted (ESC/timeout), the plugin injects synthetic `tool_result` blocks to recover the session without starting over.

### 4. Schema Sanitization
Tool schemas are cleaned via allowlist. Unsupported fields (`const`, `$ref`, `$defs`) are removed or converted to Antigravity-compatible format.

### 5. Multi-Account Load Balancing
Accounts rotate on rate limits. Gemini has dual quota pools (Antigravity headers + Gemini CLI headers). Fingerprints are per-account and regenerated on capacity exhaustion.

### 6. Fingerprint System
Per-account device fingerprints stored in `antigravity-accounts.json`. Each fingerprint includes deviceId, sessionToken, userAgent, and a reduced clientMetadata (ideType, platform, pluginType — no osVersion, arch, or sqmId). The only header composed is `User-Agent`, built by `buildFingerprintHeaders()` in `fingerprint.ts` and applied on the antigravity request path in `request.ts`. History tracked (max 5), restorable.

## Dependencies

- `zod ^4` — schema validation (NOT zod v3)
- `@opencode-ai/plugin` — OpenCode plugin interface
- `@openauthjs/openauth` — OAuth client
- `proper-lockfile` — file locking for concurrent access
- `xdg-basedir` — XDG directory resolution

## Testing

- Framework: **Vitest 3** with native ESM
- Config: `vitest.config.ts`
- Tests colocated: `src/plugin/foo.test.ts` next to `src/plugin/foo.ts`
- Use `describe`/`it`/`expect` — standard Vitest API
- Mock with `vi.fn()`, `vi.spyOn()`, `vi.mock()`

## Documentation

- [README.md](README.md) — Installation & usage
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Detailed architecture guide
- [docs/ANTIGRAVITY_API_SPEC.md](docs/ANTIGRAVITY_API_SPEC.md) — API reference
- [CHANGELOG.md](CHANGELOG.md) — Version history

---

## Python Port (Hermes Agent Plugin)

This repo also hosts a Python port at `antigravity_auth/` for Hermes Agent.

### Python Build & Test Commands

```bash
python3 -m pytest antigravity_auth/ -v          # Run all Python tests
python3 -m pytest antigravity_auth/test_config.py  # Single test file
python3 -m pytest -t "test name here"           # Single test by name
pip install -e .                                # Editable install
```

### Python Module Structure

```
antigravity_auth/
├── config.py             # YAML config loader with env var overrides
├── oauth.py              # PKCE OAuth flow
├── token.py              # Token refresh & validation
├── storage.py            # Persistent account/credential storage
├── cli.py                # CLI subcommand handlers
├── search.py             # Google Search tool via Antigravity API
├── recovery.py           # Session recovery (tool_result_missing)
├── debug.py              # Structured logging with rotation
├── verification.py       # Account health probe
├── endpoints.py          # Endpoint fallback chain
├── fingerprint.py        # Device fingerprint generation
├── constants.py          # Platform detection, default headers
├── accounts/             # Multi-account management
│   ├── manager.py        # Account rotation, selection strategies
│   ├── state.py          # Account state dataclasses
│   ├── quota.py          # Dual quota pool tracking
│   ├── ratelimit.py      # Rate limit handling & backoff
│   ├── rotation.py       # Health-score-based rotation
│   └── quota_display.py  # CLI quota progress bars
└── transform/            # Request/response transformation
    ├── messages.py       # OpenAI → Gemini contents format
    ├── thinking.py       # Claude thinking block stripping
    ├── schema.py         # JSON schema sanitization
    ├── envelope.py       # Antigravity request wrapping
    └── response.py       # SSE streaming response transformation
```

### Python Conventions
- Tests colocated (`test_*.py` next to source)
- Stdlib-only (urllib, json, dataclasses) — no heavy frameworks
- Try/except with graceful degradation (fallback values, not crashes)
- Named imports only — `from .module import Name`
- PascalCase for classes, snake_case for functions/variables
- 2-space indentation, double quotes, no semicolons
- Config via `~/.hermes/config.yaml` with `HERMES_ANTIGRAVITY_*` env overrides

### Key Differences from TypeScript Plugin
- **Config path**: `~/.hermes/` not `~/.config/opencode/`
- **Env prefix**: `HERMES_ANTIGRAVITY_*` not `OPENCODE_ANTIGRAVITY_*`
- **Plugin system**: Hermes `ProviderProfile` + `register_provider()`, not OpenCode fetch interceptor
- **No auto-update**: Hermes handles updates via pip
- **No TUI**: Hermes provides its own CLI framework
