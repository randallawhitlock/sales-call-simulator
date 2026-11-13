## What and why

<!-- One or two sentences. The PR title becomes the squash commit message, so use Conventional Commits. -->

## How it was tested

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q` (backend)
- [ ] `npm run lint && npm run typecheck && npm test && npm run build` (frontend)
- [ ] Manually checked in the browser (if UI changed)

## Checklist

- [ ] No real carrier, plan, provider or employer content
- [ ] New or changed graded rules are in `docs/rule-register.md` with cite, authority and date
- [ ] No scenario truth reaches learner endpoints or the customer prompt
