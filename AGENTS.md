# AGENTS.md

## Environment

Use the project conda environment:

```bash
conda run -n minimal-SWE-agent ...
```

Do not install dependencies into the base conda environment.

## Subagents

- Use subagents when delegation would materially improve speed, focus, or
  review quality.
- For bounded implementation, testing, and documentation tasks, prefer
  `luna_worker` with a compact, self-contained task and only the context it
  needs.
- Use the default `worker` for complex architecture, difficult debugging, or
  high-risk review work that benefits from the parent model's capabilities.
- Give each subagent clear ownership and acceptance criteria. Subagents share
  the working tree, so they must preserve and accommodate other agents' edits.
- The root agent must review subagent changes and run the required tests before
  reporting completion.

## Tests

Run non-E2E tests with:

```bash
conda run -n minimal-SWE-agent env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest tests/ -q -p no:anyio -m "not e2e"
```

Do not run E2E tests unless explicitly requested. E2E tests call a real
OpenAI-compatible API and may cost money.

## Commit Messages

Use concise but informative commit messages.

### Commit Discipline

- Before editing, confirm the current branch matches the requested delivery
  branch. Do not rename, switch, merge, or rewrite branches implicitly.
- Commit every completed logical change before starting the next one.
- Keep commits small and cohesive: one behavior or one documentation concern,
  together with only its direct tests and docs. Do not collect unrelated fixes
  into a broad catch-all commit or touch many files merely because they are
  nearby.
- Preserve unrelated working-tree changes and never absorb them into the current
  commit.
- Run focused tests before each commit. Run the full non-E2E suite before final
  completion.

Prefer this format:

```text
type(scope): summary

Explain why the change is needed.
Explain the main behavior, config, or test changes.
Mention important compatibility or migration notes when relevant.
```

Use these types:

- `feat`: new behavior or capability
- `fix`: bug fix
- `ref`: refactor without intended behavior change
- `docs`: documentation-only change
- `test`: test-only change
- `chore`: tooling or maintenance

Use scopes that match the touched area, such as:

- `model`
- `config`
- `agent`
- `tools`
- `env`
- `docs`
- `tests`

Avoid vague messages like `update stuff`, `fix config`, or `make changes`.

## Tests And Docs

When a change affects behavior, configuration, CLI usage, model-provider setup,
or public examples, update the relevant tests and docs in the same change.

For config defaults, keep these in sync when applicable:

- `src/mini_agent/config/default.yaml`
- `src/mini_agent/config/models.py`
- tests
- README/docs
