# Blue/green application releases

Scope: config-server, BFF, user, pattern and newsletter. Supabase, PostgreSQL,
Kong, storage and the static frontend keep their existing deployment lifecycle.

Each service has a persistent, unprivileged Docker Nginx router. Production and
internal clients use that router while two application versions can coexist.
The router mounts a directory containing an atomically replaced configuration.
An Nginx reload sends new connections to the healthy candidate and lets existing
connections finish on the old worker. The controller waits for those workers
and, for pattern-service, active asynchronous uploads before stopping the old
container. A busy old release stays running rather than losing work.

## Initial activation

Bootstrap each router with `python3 deploy/blue_green.py bootstrap --service NAME`
as the normal deployment user. Existing containers remain the active backends.
The reviewed `activate-host-routing.sh` requires sudo once to update three host
Nginx proxy destinations. It checks source fingerprints, saves backups, tests and
reloads Nginx, verifies the public health routes and creates a readiness marker.
On failure it restores the host configurations. Refresh fingerprints after any
intentional host configuration change. Do not create the readiness marker by
hand. Keep the existing containers until host Nginx has finished draining.

Merge the central BFF configuration first, then deploy config-server and BFF.
Deploy user, newsletter and pattern only after BFF is using the stable router
hostnames. This avoids relying on old Java clients refreshing cached legacy
container addresses during the initial migration.

## Releases and rollback

PRs build and test the application and controller without deploying. Pushes to
the default branch build an image tagged with the full commit SHA. The server
starts the inactive color, checks three consecutive healthy responses, switches
the router and observes health for 15 seconds before committing state. A failed
candidate keeps production on the old release. Config-server additionally must
fetch application configuration from its Git backend.

Readiness runs with `docker exec` inside the exact named candidate and checks
that its process has not restarted during the probe. A bridge IP can be reused
by another container after a crash, so an IP response must never authorize a
traffic switch. The Alpine images provide BusyBox wget for this private probe.

One repository workflow runs at a time, and a server lock serializes deployments
across all five services to keep memory usage bounded. Runtime overrides and
state are stored privately in `~/.local/share/knitty-blue-green`; secret values
are never printed by the controller. Shared `/security` mounts are preserved.

In GitHub Actions, run the deployment workflow with operation **rollback** to
restart and health-check the previous container before switching traffic back.
Alternatively run the controller's `rollback --service NAME` command on the host.
The previous release is retained. Still-draining older releases are retained
until a later deployment can safely remove them. Watch memory if background
work prevents several old releases from stopping.

## Compatibility requirements

Both application versions use the existing databases and storage. Schema and
API changes must support both versions during deployment and rollback. Use
expand/contract migrations: add compatible structures first, migrate callers,
and remove old structures in a later release. Automatic container rollback
does not undo database migrations. A single host remains vulnerable to host,
database or router failure; this setup protects application release cutovers.

The pattern deployment actuator endpoint is exposed only inside its Docker
service; host Nginx does not publish `/patterns/`. It reports the local upload
worker count so draining does not depend on jobs assigned to the new release.

## Verification

`python3 -m unittest discover -s deploy/tests` covers controller validation and
recovery. `python3 deploy/integration_test.py` in config-service runs isolated
Docker fixtures, continuous requests, a slow in-flight request, two releases,
an unhealthy candidate and rollback. It does not switch production services.
The fixture directory must be under the user's home for Snap Docker access.

References: [Nginx configuration reload](https://nginx.org/en/docs/control.html)
and [Spring Boot graceful shutdown](https://docs.spring.io/spring-boot/3.5/reference/web/graceful-shutdown.html).
