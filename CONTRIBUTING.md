# Contributing to Cohereny

Thank you for your interest in Cohereny.

## How development works

Development happens on a self-hosted GitLab. This GitHub repository is a **published
snapshot**: each release is pushed to `main` as a new commit, and the tested state between
releases to `next`, after every deployment to the test stage. Neither history follows the
development history, and changes made here cannot flow back automatically. To report a bug
against the newest code, `next` is the branch to check first.

## What helps most right now

**Issues are welcome.** Open one for:

- a bug, ideally with the request, the response and what you expected
- a use case the architecture does not cover
- a concern about a design decision — the [architecture document](https://github.com/cohereny/cohereny/blob/main/docs/cohereny-arc42.md)
  records the reasoning behind each one, which makes it easier to point at the step you
  disagree with
- a question about the regulatory positioning

## Pull requests

**Pull requests cannot be merged yet.** Two reasons:

1. This repository is a snapshot; a merged change would be overwritten by the next one.
2. Cohereny is dual-licensed (AGPLv3 and commercial). Accepting a contribution requires a
   Contributor License Agreement, so that the contributed code can be offered under both
   licenses. The agreement is not in place yet.

If you have a fix, open an issue and describe it — or link your branch. It will be
re-implemented upstream and credited.

## Conventions

Code, comments and commit messages are in English. The coding conventions are in
[CONVENTIONS.md](CONVENTIONS.md) (German); most of them are enforced in CI.

## Code of conduct

Be respectful. Criticism of the work is welcome; personal attacks are not.

## Maintainer

Thomas Roth — [cohereny.com](https://cohereny.com) · contact@cohereny.com
