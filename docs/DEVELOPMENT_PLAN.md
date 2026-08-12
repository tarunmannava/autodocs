# AutoDocs Development Plan

This document is the working roadmap for building AutoDocs incrementally. The project is intentionally local-first: FastAPI, workers, repository analysis, and agent execution run locally. Supabase is the hosted persistence layer. GitHub reaches the local FastAPI server through an HTTPS tunnel during development.

## 1. Current status

### Current phase: Phase 1 — Webhook intake and persistence

We are currently at the end of the first vertical slice of Phase 1:

- FastAPI application exists.
- `/health` is available.
- GitHub webhook signature verification works.
- GitHub ping delivery succeeds through ngrok.
- The webhook processes only merged pull requests:
  - `X-GitHub-Event: pull_request`
  - `action: closed`
  - `pull_request.merged: true`
- Supabase connectivity has been verified with a real query.
- A real signed webhook successfully created rows in:
  - `webhook_deliveries`
  - `runs`
- Temporary verification rows were removed.
- Controller/service/accessor/infrastructure layering is in place.
- Pydantic models exist for the initial database entities.
- The current queue is an in-memory queue.
- The repository is not cloned yet.
- No diff is generated yet.
- No LLM or documentation agent is connected yet.

### Current production-like flow

```text
GitHub merged PR
    |
    v
ngrok HTTPS tunnel
    |
    v
FastAPI controller
    |
    v
Webhook service
    |
    +--> Supabase webhook_deliveries
    |
    +--> Supabase runs
    |
    +--> In-memory queue
```

### Current known limitations

- The inline queue is process-local and not durable.
- A queued job is not processed after it is added.
- Repository cloning and diff generation are not implemented.
- The GitHub webhook currently targets merged PRs only by design.
- The local `.env` must contain the real Supabase URL and rotated server secret.
- The Supabase tables must be exposed to the Data API for the Python client.
- Tests should use `create_app(use_supabase=False)` and must not write to the real database.

## 2. Local development setup

### Required processes

| Process | Purpose | Command |
|---|---|---|
| FastAPI | Webhook receiver | `python -m uvicorn backend.main:app --reload --port 8000` |
| ngrok | Public HTTPS tunnel for GitHub | `ngrok http --url=dad-kilobyte-morality.ngrok-free.dev 8000` |
| Redis | Durable queue in later phase | `docker compose -f infra/docker-compose.yml up -d redis` |
| Celery worker | Background processing in later phase | Project command to be added in Phase 2 |

### Local URLs

```text
FastAPI health: http://127.0.0.1:8000/health
FastAPI docs:   http://127.0.0.1:8000/docs
Public webhook: https://dad-kilobyte-morality.ngrok-free.dev/webhooks/github
```

### Required local environment variables

```env
AUTODOCS_ENVIRONMENT=development
AUTODOCS_GITHUB_WEBHOOK_SECRET=local-github-webhook-secret
AUTODOCS_QUEUE_BACKEND=inline
AUTODOCS_REDIS_URL=redis://localhost:6379/0

SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_PUBLISHABLE_KEY=your-publishable-key
SUPABASE_SECRET_KEY=your-rotated-server-secret-key
SUPABASE_JWKS_URL=https://your-project-ref.supabase.co/auth/v1/.well-known/jwks.json
```

Never commit `.env`. Never use the Supabase secret key in frontend code or in webhook configuration. The GitHub webhook secret and Supabase secret key are different credentials.

## 3. Development principles

1. Build one working vertical slice before expanding the agent graph.
2. Keep controllers thin; business rules belong in services.
3. Keep Supabase calls inside accessors.
4. Keep external integrations behind infrastructure interfaces.
5. Persist state transitions so every run is inspectable.
6. Make webhook delivery handling idempotent.
7. Do not send the whole repository to an agent; build scoped context.
8. Do not run repository code directly on the host once analysis begins; add a sandbox boundary before executing project tooling.
9. Every phase has a demonstrable acceptance test.
10. Keep real Supabase writes out of automated unit tests.

## 4. Orchestrator and child-agent architecture

The long-term agent system is an orchestrator with conditional fan-out and fan-in. It is not a chain that invokes every agent for every pull request.

### Runtime graph

```text
Run created
    |
    v
Repository analysis
    |
    v
Change graph builder
    |
    v
Planner / orchestrator
    |
    +-- no relevant changes --> mark skipped and finish
    |
    +-- API context ---------> API agent -----------+
    +-- README context ------> README agent --------+
    +-- architecture context -> Architecture agent -+
    +-- config context ------> Config agent --------+
    +-- migration context ---> Migration agent ------+--> Release Notes agent
                                                       |
                                                       v
                                             Consistency agent
                                                       |
                                                       v
                                             Documentation builder
                                                       |
                                                       v
                                             Publisher / PR comment
```

### Orchestrator responsibilities

The orchestrator owns the run-level workflow:

1. Load the run and parsed change graph.
2. Classify the merged change.
3. Select only relevant child agents.
4. Build a separate bounded context bundle for each selected agent.
5. Fan out selected agents concurrently with a concurrency limit.
6. Persist each child-agent status and structured output.
7. Wait for required child outputs.
8. Invoke Release Notes after specialist outputs are available.
9. Invoke Consistency after all relevant outputs are available.
10. Pass resolved outputs to the Documentation Builder.
11. Publish or stop for human review when consistency flags are blocking.

The orchestrator should be implemented as a LangGraph state machine once the raw-diff vertical slice works. Celery is responsible for durable execution; LangGraph is responsible for the agent workflow inside a run.

### Child-agent contract

Every child agent receives:

- `run_id`
- Repository and pull-request metadata
- A narrowly scoped graph subgraph
- Relevant changed files and line ranges
- Relevant existing documentation
- Agent-specific instructions
- Prompt/model version

Every child agent returns:

- Agent name
- Status: completed, skipped, or failed
- Typed structured output
- Proposed file-level changes
- Confidence and evidence references
- Token usage and duration
- Error information when applicable

Child agents must not mutate the repository or database directly. They return proposals to the orchestrator. The builder is the only component that merges proposals into files.

### Conditional routing rules

Use deterministic graph rules before asking the planner model to make a judgment:

| Change signal | Child agent |
|---|---|
| Public endpoint, schema, or signature changed | API |
| User-facing behavior or usage changed | README |
| Module boundaries or dependencies changed | Architecture |
| Configuration key/schema/default changed | Config |
| Breaking or migration-required change | Migration |
| Any accepted specialist output | Release Notes |
| Two or more outputs exist | Consistency |

A test-only or internal-only merged PR should produce zero specialist tasks. Release Notes and Consistency should not run when there are no specialist outputs.

### Failure and retry behavior

- A failed child agent is persisted independently.
- Retry only the failed child agent, not the entire run.
- Use a maximum retry count and exponential backoff.
- Continue when an optional specialist fails.
- Stop before publishing when a required agent or Consistency check fails.
- Never publish unresolved contradictory documentation automatically.
- Make each node idempotent using `(run_id, agent_name, attempt)`.

### State model

The LangGraph state should contain references, not an entire repository:

```text
run_id
repository
pr_number
base_sha
head_sha
change_summary
changed_files
change_graph_reference
planner_decision
agent_tasks
agent_outputs
consistency_flags
builder_result
publish_result
```

Large diffs, source files, and generated artifacts should remain in local job storage or Supabase Storage and be loaded only by the node that needs them.

### What exists now versus what is planned

Currently implemented:

- Webhook controller
- Webhook service
- Supabase accessors
- In-memory queue
- Typed database models

Not implemented yet:

- LangGraph
- Planner node
- Child-agent fan-out
- Agent prompt/tool execution
- Release Notes fan-in
- Consistency agent
- Documentation builder
- GitHub documentation PR publisher

## 5. Weekly roadmap

## Week 1 — Finish webhook-to-run vertical slice

### Goal

Make a merged GitHub PR reliably create an idempotent run in Supabase and hand it to a durable queue boundary.

### Tasks

- Confirm GitHub webhook configuration:
  - Pull requests enabled.
  - Secret matches `AUTODOCS_GITHUB_WEBHOOK_SECRET`.
  - SSL verification enabled.
- Add explicit duplicate-delivery tests.
- Add Supabase accessor tests with mocked clients.
- Add run status update methods:
  - `mark_cloning`
  - `mark_diffing`
  - `mark_failed`
- Add a run lookup by delivery ID.
- Add structured logging with delivery ID, repository, PR number, and run ID.
- Add a health response that reports configuration state without exposing secrets.
- Decide whether raw diffs remain in `runs.raw_diff` for MVP or move to Storage.

### Acceptance criteria

- A merged PR creates exactly one `webhook_deliveries` row.
- Redelivering the same GitHub delivery does not create another run.
- A merged PR creates exactly one `runs` row.
- Closed but unmerged PRs are ignored.
- Unit tests do not access the real Supabase project.
- The live webhook can be demonstrated through ngrok.

## Week 2 — Durable local queue and repository manager

### Goal

Move work out of the HTTP request and process it asynchronously on the local machine.

### Tasks

- Add Redis to local Docker Compose.
- Add Celery configuration and a worker entrypoint.
- Keep the inline queue as a test double.
- Add `RepositoryManager` service and accessor/infrastructure boundary.
- Implement shallow clone or fetch:
  - Repository cache directory.
  - `--depth=50` initially.
  - Safe repository-name normalization.
- Checkout the PR head SHA.
- Resolve and fetch the base SHA.
- Add cleanup and locking for concurrent runs.
- Update run statuses around each repository operation.

### Acceptance criteria

- Webhook returns before repository work completes.
- Worker receives a serialized `PullRequestJob`.
- A test repository is cloned locally.
- The exact PR head SHA is checked out.
- A failed clone marks the run as `failed` with an actionable error.

## Week 3 — Raw diff generation and parsing

### Goal

Produce and persist a trustworthy representation of the merged PR changes.

### Tasks

- Generate the diff between base SHA and head SHA.
- Store the raw unified diff in Supabase for small diffs.
- Store large diffs as artifacts and retain a reference in `runs`.
- Parse unified diffs with `unidiff`.
- Add changed-file models:
  - Path
  - Added lines
  - Removed lines
  - Added/deleted flags
  - Changed line ranges
- Classify files:
  - Source code
  - Tests
  - Documentation
  - Configuration
  - Generated files
  - Lockfiles
- Add binary-file and rename handling.

### Acceptance criteria

- A real merged PR produces a raw diff.
- The diff lists added, modified, deleted, and renamed files correctly.
- The parsed output is stored against the run.
- Empty or malformed diffs fail clearly.

## Week 4 — Thin documentation vertical slice

### Goal

Before building the full graph, pass a scoped raw diff to one README agent and report the result.

### Tasks

- Add Anthropic client abstraction.
- Add an agent call service with timeout and retry limits.
- Add `README` agent input/output Pydantic models.
- Give the agent only:
  - Parsed diff
  - Relevant changed files
  - Existing README sections
- Persist the structured result in `agent_outputs`.
- Post a summary comment on the original GitHub PR.
- Do not create a documentation branch yet.

### Acceptance criteria

- A merged PR that changes user-facing behavior produces a README proposal.
- A code-only internal change can produce a skipped result.
- Agent output is valid structured JSON.
- Agent failure is persisted and does not lose the run record.
- The original PR receives a concise status comment.

## Week 5 — Symbol-level change detection

### Goal

Identify changed functions, classes, endpoints, and configuration symbols instead of passing only file-level changes.

### Tasks

- Add tree-sitter language parser registry.
- Start with Python and JavaScript/TypeScript.
- Parse changed files at both base and head revisions.
- Map changed line ranges to symbols.
- Add AST symbol models.
- Handle parse errors without failing the entire run.
- Add language detection and unsupported-language reporting.

### Acceptance criteria

- A changed function is identified by name and file path.
- A changed class is identified by name and file path.
- Added and removed symbols are represented.
- Unsupported languages are marked as unsupported rather than silently skipped.

## Week 6 — Change graph

### Goal

Build a graph of changed and affected code.

### Tasks

- Add `change_graph_nodes` table.
- Add `change_graph_edges` table.
- Build file, module, symbol, endpoint, and config-key nodes.
- Add import and containment edges first.
- Add reference and call edges where language support is reliable.
- Persist graph nodes and edges per run.
- Add N-hop graph traversal.
- Mark changed nodes versus affected nodes.
- Add graph query helpers for agent context.

### Acceptance criteria

- Every changed file appears in the graph.
- Changed symbols are connected to their containing file/module.
- Direct imports are represented.
- A scoped N-hop subgraph can be returned for one documentation type.
- Graph persistence is idempotent per run.

## Week 7 — Documentation planner

### Goal

Route only relevant work to specialist agents.

### Tasks

- Add planner input model.
- Add planner output model:
  - PR classification
  - Agents to run
  - Per-agent context bundle
  - Confidence/reasoning metadata
- Implement deterministic pre-routing rules before LLM routing:
  - API files → API agent
  - Config schema → Config agent
  - User-facing behavior → README agent
  - Structural changes → Architecture agent
  - Breaking changes → Migration agent
- Use the LLM planner only where graph rules are insufficient.
- Add zero-agent path for test-only/internal-only changes.
- Persist planner output.

### Acceptance criteria

- Test-only PRs run zero specialist agents.
- API changes route to the API agent.
- Config changes route to the Config agent.
- Breaking changes route to the Migration agent.
- Every selected agent receives a bounded context bundle.

## Week 8 — Specialist agents

### Goal

Implement specialists one at a time with typed outputs.

### Order

1. Config agent
2. API agent
3. README agent
4. Architecture agent
5. Migration agent
6. Release Notes agent

### Tasks for every agent

- Define input and output Pydantic models.
- Define allowed source files and docs.
- Define tools/read access narrowly.
- Add prompt versioning.
- Add timeout and retry policy.
- Persist token usage and duration.
- Add fixture-based tests using historical diffs.

### Acceptance criteria

- Each specialist produces valid structured output.
- Each agent can be skipped cleanly.
- Each output identifies proposed file changes rather than returning unconstrained prose.
- Agent output can be replayed from stored input.

## Week 9 — Consistency agent and documentation builder

### Goal

Merge specialist proposals into coherent file-level documentation changes.

### Tasks

- Add `consistency_flags` table.
- Detect conflicting defaults, versions, terminology, and links.
- Resolve safe conflicts deterministically.
- Flag unsafe conflicts for review.
- Add documentation builder.
- Apply Markdown formatting rules.
- Validate links and generated OpenAPI/JSON where applicable.
- Generate file-level diffs.
- Ensure one file is not overwritten by competing agent proposals.

### Acceptance criteria

- Intentionally conflicting outputs create a consistency flag.
- Resolved output has one canonical value per fact.
- Builder produces deterministic file diffs.
- Markdown and structured-document validation pass.

## Week 10 — GitHub publishing

### Goal

Create a documentation branch and reviewable documentation PR.

### Tasks

- Add GitHub App authentication.
- Add repository branch creation.
- Add commit creation/push.
- Create or update `docs/pr-<number>` branch.
- Open a documentation PR linked to the original PR.
- Post a summary comment on the original PR.
- Make publishing idempotent.
- Handle existing documentation PRs.
- Add dry-run mode.

### Acceptance criteria

- A complete run creates a documentation branch.
- The generated commit contains only approved documentation changes.
- A documentation PR is opened or updated.
- The original PR receives a summary with changed files and links.
- Re-running the same delivery does not create duplicate PRs.

## Week 11 — Security, sandboxing, and observability

### Goal

Make local processing safer and failures diagnosable before broad use.

### Tasks

- Run repository analysis in an isolated container.
- Restrict network egress.
- Avoid executing untrusted repository code by default.
- Add structured logs and correlation IDs.
- Add agent traces with Langfuse or LangSmith.
- Track token usage and estimated cost.
- Add timeouts and concurrency limits.
- Add GitHub rate-limit handling.
- Add secret redaction in logs.
- Add cleanup for clone directories and artifacts.

### Acceptance criteria

- Repository contents cannot write outside the job workspace.
- Secrets do not appear in logs.
- Every run can be traced from webhook delivery to published output.
- A failed agent can be retried without repeating completed work.

## Week 12 — Evaluation and quality loop

### Goal

Measure whether the system produces useful documentation, not merely valid output.

### Tasks

- Collect 20–30 historical merged PRs.
- Define ideal documentation changes.
- Create a repeatable evaluation runner.
- Score:
  - Correctness
  - Completeness
  - Scope discipline
  - Terminology consistency
  - Link validity
  - Reviewer usefulness
- Add human review labels.
- Tune planner and specialist prompts from failures.
- Establish regression fixtures.

### Acceptance criteria

- Historical PRs can be replayed.
- Outputs are scored consistently.
- Prompt or routing changes can be compared against a baseline.
- Major failure modes have regression tests.

## 6. Database migration order

Create the database schema in this order:

### Already planned/initial tables

1. `webhook_deliveries`
2. `runs`
3. `agent_outputs`
4. `published_docs`

### Add later

5. `change_graph_nodes`
6. `change_graph_edges`
7. `consistency_flags`

Keep RLS enabled. Since AutoDocs is backend-only for now, do not create public `anon` or `authenticated` policies. Use the server-side Supabase secret only from FastAPI/Celery. Expose the tables to the Data API and grant least-privilege access required by the backend.

## 7. Recommended service boundaries

```text
controllers/
  HTTP and webhook transport only

services/
  Business workflows and state transitions

accessors/
  Supabase persistence and repository data access

infrastructure/
  Supabase client, queue, GitHub API, Git process, LLM client

models/
  Pydantic domain, input, output, and database models

agents/
  Planner, specialists, consistency, and graph orchestration
```

The controller should never call Supabase directly. The service should never know whether persistence is Supabase or in-memory. Accessors should return typed models rather than raw dictionaries.

## 8. Testing strategy

### Unit tests

- Signature verification
- Payload parsing
- Merge filtering
- Duplicate delivery behavior
- Pydantic validation
- Diff parsing
- Graph traversal
- Planner routing rules
- Agent output validation
- Builder conflict handling

### Integration tests

- FastAPI webhook with in-memory accessors
- Supabase accessors with a test project or isolated schema
- Local Redis/Celery worker
- Repository clone and diff fixture
- GitHub API client mocked at the network boundary

### End-to-end tests

- Real GitHub ping through ngrok
- Real merged PR webhook to local FastAPI
- Supabase run and delivery persistence
- Local worker processing
- Documentation PR creation only in a dedicated test repository

## 9. Definition of done for the first real milestone

The first meaningful milestone is not the full six-agent system. It is:

```text
Merged GitHub PR
  → verified webhook
  → Supabase run
  → local background worker
  → shallow clone
  → raw diff
  → README proposal
  → GitHub comment
```

Once this works reliably, the graph, planner, additional agents, consistency layer, and documentation PR publishing can be added incrementally.
