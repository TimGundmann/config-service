# Workspace review — 11 November 2026

Scope: the 16 unique Git repositories checked out under `D:/work/Knittynetwork`
and `D:/work/toiletpilot`, plus the second `gundmann-config` checkout under
`D:/work/knittinetwork`. Historical GitHub-only projects were inventoried but
are outside this implementation pass. Default branches are `main` or `master`.

## Changes

- Integrated the outstanding pattern reuse, hourly-limit frontend/BFF and
  Docker incident-monitoring branches. Compared content and patch equivalence
  to avoid reintroducing obsolete branches already squash-merged.
- Security starter: reject the development sentinel when security is enabled;
  remove token logs; run the reactive authentication chain once. Consumers
  bff, pattern-service, user-service and newsletter-service use version 1.0.89.
- Frontends: remove authentication/session/profile debug output, fix lint
  errors without weakening rules, encode image URL/marker HTML attributes,
  preserve literal replacement strings, and sanitize pattern descriptions
  through the existing Jsoup dependency at API output. Stored recipe source,
  safe formatting and instruction marker IDs remain intact.
- Feed: separate images from tags; handle absent lists; fix JPA entity
  constructors, builder-generated IDs and the quoted reserved `like` table.
  Verify a real save/flush/clear/reload of the related entities.
- User service: return newly created profiles; use gauge IDs from route paths;
  refuse reading or overwriting another user's legacy gauge before mutation.
- Move instruction-count validation and newsletter email validation to their
  domain objects. Apply constructor/method visibility ordering and one-call-
  per-line chaining in handwritten Java production code and tests.
- Restore the real InstructionDetection prompt after finding a local test stub.
  Keep the two intended image/classification prompt updates. Preserve the stub
  outside Git in the local review-backups directory.
- Configuration: Maven wrapper 3.9.12; expose only health/info Actuator endpoints;
  preserve maintained local monitoring scripts; install incident modules with
  the collector; run tests before package publication and frontend deployment.
- Toiletpilot: use real list endpoint/query parameters and Spring Page.content;
  send mandatory city and boolean accessibility; remove unsupported form
  features and align browser tests with the real API contract. Repair the
  inconsistent npm lockfile without introducing dependencies.

## Verification

| Repository | Local verification |
| --- | --- |
| bff | 37 tests passed, 1 pre-existing skipped |
| pattern-service | 517 passed, 7 pre-existing skipped |
| user-service | 15 passed |
| newsletter-service | 6 passed |
| feed-service | 6 passed, including JPA roundtrip |
| security starter | 15 passed; version 1.0.89 publication passed on GitHub |
| config-service | Maven verify: 2 passed; monitoring: 18 passed |
| pattern-api | Java modules compile; 2 client contract tests passed |
| user-api | Java modules compile; no existing test sources |
| frontend | 7 passed; lint, TypeScript and production build passed |
| knottynetwork | 7 passed; lint, TypeScript and production build passed |
| knottynetwork-frontend | 117 passed; lint, TypeScript and build passed |
| gundmann-config | YAML and nested prompt payloads validated |
| knitty-deploy-actions | Composite action YAML and retry conditions reviewed |
| toiletpilot-server | 3 passed, 2 existing Docker-dependent tests skipped |
| toiletpilot-frontend | 2 API tests and 3 Chrome tests passed; build passed |

Regression tests reproduced the defects before their fixes. No failing tests
were disabled. Generated API code, databases, PDFs, credentials, build output
and Python caches are excluded from commits. The Linux-only deployment suite
requires `fcntl` and cannot execute on this Windows host; its GitHub PR checks
are the validation path for that suite.

## Remaining follow-up

1. `config-service/src/main/resources/application.yaml` contains a historical
   hardcoded encryption key in a public repository. Replacing it requires a
   coordinated runtime secret change and re-encryption of existing ciphertext.
   It was not blindly changed in this source-only review. Treat it as exposed.
2. The legacy user-service Gauge table is keyed only by pattern ID. Ownership
   checks now prevent cross-user disclosure/overwrite, but separate gauges for
   multiple users of the same pattern require a database migration to a
   composite user/pattern identity. The second user is rejected safely today.
3. The existing skipped tests, Fast Refresh warnings and large frontend chunks
   remain visible. The review does not substitute for production data/schema
   migration verification.
