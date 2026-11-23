# Security

Jellyscope holds a few things worth protecting: your **Jellyfin API key**,
the keys of the apps you connect (**Sonarr, Radarr, Lidarr, Readarr, Bazarr,
Prowlarr, Seerr**), its own **API keys**, and the **accounts** people sign in
with. Please report anything that puts any of them at risk.

## Reporting a vulnerability

Open a [security advisory](https://github.com/SpeeDFireCZE/jellyscope/security/advisories/new).
It stays private until there is a fix. Please do **not** open a normal issue
for a vulnerability, because issues are public the moment you press the
button.

Tell me what you did, what happened, and what you expected. A proof of concept
helps but is not required. Expect a first answer within a few days; this is a
one-person project, not a company with an on-call rota.

## What the app already does

Worth knowing before you report, and worth checking if you are reviewing:

- everything is behind a login; the only pages open without one are the login
  and setup forms and a `/health` endpoint that says only "alive"
- passwords are stored as PBKDF2-SHA256 hashes, 600 000 iterations, salted
- repeated failed logins block the address, each block longer than the last
  (1, 2, 5 and 15 minutes), and after the fourth one permanently; an address
  that behaves for a day starts again from the first level
- the session cookie is signed with a key that is generated on first start
  and stored in `data/secret_key`; there is no fallback value in the source
- the Jellyfin API key never reaches the browser, travels in a header (not a
  query string, so it stays out of proxy logs) and is masked in the log viewer
- the keys of the connected apps are never shown back, and they and any
  credentials in an app's address are scrubbed from every error text; the
  apps' own messages (which carry paths and internal addresses) are shown to
  administrators only
- Jellyscope's own API keys are shown once and stored as a hash; a key can do
  at most what its owner or its root-key rights allow, and every request with
  an existing key is logged
- every SQL query uses parameters; `ffprobe` runs without a shell
- pages run scripts only from the app itself (a strict
  Content-Security-Policy, no inline JavaScript), and responses carry
  `X-Frame-Options`, `Referrer-Policy`, a Permissions-Policy and, with
  `SECURE_COOKIES` on, HSTS
- Jellyfin is only ever read from; nothing in the code writes to it. The
  connected apps are only read from too, with one exception: while the
  Downloading card is open, an \*Arr app is asked to refresh its download
  list

## Scope

In scope: authentication, session handling, permissions, the handling of the
keys above, injection of any kind, and anything reachable from the browser or
through the API.

Out of scope: running the app on `0.0.0.0` without a reverse proxy (that is a
documented choice, see [DEPLOY.md](DEPLOY.md)), and vulnerabilities in
Jellyfin, the \*Arr apps or Seerr themselves.

## Supported versions

| Version | Supported |
|---|---|
| 1.9.x | yes |
| 1.8.x and older | no |

Fixes go into the latest release and the latest commit on `main`. This is a
small project, and there are no maintained release branches, so the fix for
an older version is to update. Going from 1.8.x to 1.9.0 converts the
database; see [Updating to 1.9.0](README.md#updating-to-190-english-names-in-the-database)
in the README.
