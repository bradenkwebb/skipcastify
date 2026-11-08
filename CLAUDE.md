# Skipcastify

Removes ads from podcast episodes and serves ad-free feeds. One `pipeline.py`
run walks five stages — download, process, cleanup, feeds, publish — on a
systemd timer. See README.md for setup and the stage detail.

## Commands

```bash
uv sync                       # install
uv run pytest                 # standard suite, no LLM calls
uv run pytest --run-llm       # LLM regression tests: real API calls, costs money
uv run python pipeline.py      # one pipeline run
uv run python scripts/eval_ads.py --runs 3   # score detection against hand labels
```

Use `uv`; never install into a global Python.

## Layout

- `pipeline.py` — stage orchestration, storage budget, run log
- `skipcastify/services/` — `download_episode`, `audio_processor` (transcribe +
  LLM detection), `span_postprocess`, `section_processor`, `publisher`, `ad_eval`
- `skipcastify/llm_prompts/identify_ads_prompt.txt` — the detection prompt,
  edited independently of the code
- `server.py` + `templates/` — feeds, dashboard, labeling UI
- `tests/llm/` — one file per feed, against committed transcript fixtures

## Working on ad detection

The model is non-deterministic at `temperature=0` on a pinned snapshot, and its
dominant output shape drifts over hours. Two consequences, both load-bearing:

- **A single run is a sample, not a measurement.** Take rates over several runs.
- **Compare prompts by interleaving the arms round-robin**, never one arm after
  the other — a sequential A/B measures the clock as much as the prompt.
  `scripts/eval_ads.py` does this; `tests/llm/conftest.py` records the rule.

Record known misses as `xfail(strict=False)`. A `strict=True` marker on an
LLM assertion fails the suite when the model improves.

An empty span list is a real answer, meaning the episode has no ads. The
keyword heuristic in `segment_classifier.py` runs only when every LLM section
call fails — never as a second opinion on an LLM that answered.

Prefer a worked example in the prompt over a stated rule: examples have moved
this model where rule text repeatedly did not.

## Conventions

- **Comments: one or two lines.** Incident history, rejected alternatives and
  the reasoning behind a fix belong in the commit message, the PR body, or a
  test docstring that names the case — not in an inline block.
- **One meaningful change per commit.** No bundling.
- No `Co-Authored-By: Claude` or `Claude-Session:` trailers in commits.
- Develop in a git worktree. The server's main checkout is live and
  timer-driven; editing it in place changes what is running.

## Secrets

`.env` and `*.opml` are gitignored and must never be committed — the OPML file
carries a private feed token. Don't print secret values when reading `.env`,
and redact tokens when showing feed or OPML content. The public base URL and
tailnet host stay out of committed files; `.env.example` holds placeholders.
