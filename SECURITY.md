# Security

Jellyscope holds two things worth protecting: your **Jellyfin API key** and
the **accounts** people sign in with. Please report anything that puts either
at risk.

## Reporting a vulnerability

Open a [security advisory](https://github.com/SpeeDFireCZE/jellyscope/security/advisories/new)
— it stays private until there is a fix. Please do **not** open a normal issue
for a vulnerability; issues are public from the second you press the button.

Tell me what you did, what happened, and what you expected. A proof of concept
helps but is not required. Expect a first answer within a few days — this is a
one-person project, not a company with a rota.

## What the app already does

Worth knowing before you report, and worth checking if you are reviewing:

- everything is behind a login; there is no anonymous page except the login
  and setup forms and a `/health` endpoint that says only "alive"
- passwords are stored as PBKDF2-SHA256 hashes, 600 000 iterations, salted
- repeated failed logins block the address, each block longer than the last,
  the fourth one permanently
- the session cookie is signed with a key that is generated on first start
  and stored in `data/secret_key` — there is no fallback value in the source
- the Jellyfin API key never reaches the browser, travels in a header (not a
  query string, so it stays out of proxy logs) and is masked in the log viewer
- every SQL query uses parameters; `ffprobe` runs without a shell
- Jellyfin is only ever read from — nothing in the code writes to it

## Scope

In scope: authentication, session handling, permissions, the API-key handling
above, injection of any kind, and anything reachable from the browser.

Out of scope: running the app on `0.0.0.0` without a reverse proxy (that is a
documented choice, see [DEPLOY.md](DEPLOY.md)), and vulnerabilities in Jellyfin
itself.

## Supported versions

The latest commit on `main`. This is a small project; there are no maintained
release branches.
