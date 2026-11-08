# Contributing

Thanks for taking a look. The project is early, so issues and discussion are more useful right
now than large pull requests. If you want to build something, open an issue first so we can
agree on the approach.

## Ground rules

- **Fictional content only.** No real carrier names, plan IDs, provider identities, employer
  scripts, scorecards or screenshots. Everything in `content/` is invented and labeled as
  training material.
- **Cite regulatory claims.** A rule that affects grading needs a public source, a paragraph
  cite, an authority type and a verified-as-of date in [the rule register](../docs/rule-register.md).
  If you can't verify it, mark it `unverified`.
- **Keep the answer key server-side.** Nothing from scenario truth may reach a learner endpoint
  or the AI customer's prompt. The leak tests will catch it, but please don't make them.

## Workflow

`main` is protected: every change goes through a pull request and is squash-merged, so the PR
title becomes the commit message. Use [Conventional Commits](https://www.conventionalcommits.org/)
for PR titles, for example `feat(engine): add reveal rules to customer knowledge`.

1. Branch from `main` (`feat/...`, `fix/...`, `docs/...`, `chore/...`).
2. Run the checks below locally.
3. Open a PR using the template and make sure CI is green.

## Local checks

```bash
cd backend && uv run ruff check . && uv run pytest -q
cd frontend && npx tsc --noEmit && npm run build
```

Tests that call real model APIs are opt-in: `uv run pytest -m live`.
