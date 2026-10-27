# Releasing

Publishing is automated. **Merging `dev` into `main` publishes whatever version
`pyproject.toml` declares.** Nothing is published from `dev`, and nothing is
published by hand.

## One-time setup

The workflow uses PyPI **Trusted Publishing** (OIDC) — there is no API token to
create, store or rotate.

1. **Add the trusted publisher** — pypi.org → *Your projects* → `inverge-nexus` →
   **Manage** → **Publishing** → *Add a new publisher* → **GitHub**:
   - Owner: `Inverge-team`
   - Repository name: `nexus-python`
   - Workflow name: `release.yml`
   - Environment name: `release`

   All four must match exactly, or the upload is rejected with
   `invalid-publisher`.
2. **Create `main`** from `dev` (see the note at the bottom).

The `release` GitHub environment is created on the first run with no protection
rules. Add required reviewers there if you want a human to approve each publish.

## Cutting a release

1. On `dev`, set `version` in `pyproject.toml` (PEP 440: `1.6.0`, or a
   pre-release such as `1.6.0rc1` — a pre-release is marked as such on the GitHub
   Release, and pip ignores it unless asked).
2. Add the version's section to `CHANGELOG.md`.
3. Merge `dev` → `main`.

`.github/workflows/release.yml` then runs the test suite on **every supported
Python (3.8 – 3.13)**, builds the sdist + wheel, checks them with
`twine check --strict`, uploads to PyPI, then tags `v<version>` and opens a GitHub
Release with the distributions attached.

Tests run **before** the upload. A red build publishes nothing.

## Re-running is safe

A version on PyPI can never be replaced — even a deleted one can't be re-uploaded.
The workflow is built around that:

- Before anything else it asks PyPI whether the version already exists. If it does,
  every later job is skipped and the run still succeeds, so an unrelated commit to
  `main` does not fail and cannot attempt a doomed re-upload.
- The upload uses `skip-existing`, so a run interrupted halfway can be re-run.
- The tag and Release are only created if they do not already exist.

To release again you must **bump the version**.

## Publishing by hand

You should not need to, but `workflow_dispatch` is enabled, so you can run the
Release workflow manually against `main` from the Actions tab.

## A note on `main`

The umbrella `CLAUDE.md` carries a hard rule — *never push `main`* — because on the
**backend** repo `main` auto-deploys production. In this SDK repo `main` is the
release branch and pushing to it is the intended way to publish. Even so, an agent
working here will not create or push `main` for you; create it yourself and do the
merges, so a publish is always a deliberate human act:

```bash
cd SDKs/nexus-python
git checkout -b main dev
git push -u origin main        # this triggers the first release
git checkout dev
```

Afterwards, releases are ordinary merges of `dev` into `main`.
