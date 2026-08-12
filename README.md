# AutoDocs - Intelligent Multi-Agent Documentation System

A **PR-triggered, multi-agent graph-routed source-code documentation platform** that turns GitHub pull-request changes into scoped, reviewable documentation updates automatically.

The application ingests GitHub `pull_request` events, verifies security via HMAC-SHA256 signatures, deduplicates deliveries, parses unified git diffs, enqueues work onto Celery/Redis, and routes code changes through a multi-agent graph to update technical documentation.

---

## Table of Contents

- [Overview](#overview)
- [Current Features](#current-features)
- [System Architecture](#system-architecture)
- [End-to-End Workflow](#end-to-end-workflow)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Core Components](#core-components)
- [Installation](#installation)
- [Environment Configuration](#environment-configuration)
- [Docker & Services Setup](#docker--services-setup)
- [Running the Application](#running-the-application)
- [Exposing via Ngrok](#exposing-via-ngrok)
- [API Overview](#api-overview)
- [Database & Storage Schema](#database--storage-schema)
- [PR Webhook Lifecycle](#pr-webhook-lifecycle)
- [Testing & Quality Assurance](#testing--quality-assurance)
- [Troubleshooting](#troubleshooting)
- [Current Limitations](#current-limitations)
- [Planned Enhancements](#planned-enhancements)

---

## Overview

Software projects change rapidly as pull requests are merged, but documentation frequently drifts out of sync. Developers must manually track modified code files, identify affected concepts or APIs, rewrite markdown guides, and post summary updates.

**AutoDocs** automates this workflow directly from your GitHub CI/CD pipeline.

When a developer merges a Pull Request on GitHub:

```text
GitHub Pull Request Merged
            |
            v
HMAC Webhook Verification (/webhooks/github)
            |
            v
Deduplication & Event Filtering (Supabase)
            |
            v
Background Task Queueing (Celery + Redis)
            |
            v
Repository Checkout & Unified Diff Parsing (unidiff)
            |
            v
Graph-Routed Multi-Agent Pipeline
            |
            v
Scoped Documentation Update & GitHub PR Summary
```

AutoDocs combines **deterministic static diff parsing** with **asynchronous task execution** and **graph-routed AI documentation agents**.

---

## Current Features

### 🔐 HMAC-SHA256 Webhook Verification
- Validates the `X-Hub-Signature-256` header on incoming GitHub webhook requests using your configured secret (`AUTODOCS_GITHUB_WEBHOOK_SECRET`).
- Rejects unauthenticated or tampered requests immediately with HTTP 401 Unauthorized.

### 🛡️ Event Filtering & Deduplication (Idempotency)
- Specifically targets merged pull requests (`action: "closed"` with `merged: true`).
- Ignores unmerged or irrelevant event types safely with HTTP 202 Accepted.
- Deduplicates deliveries using Supabase (`webhook_deliveries` table) to prevent re-processing identical GitHub deliveries.

### ⚡ Dual-Queue Architecture
- **`InlineJobQueue`**: In-memory synchronous queue for rapid local testing and development.
- **`CeleryQueue`**: Production-ready asynchronous task worker powered by Redis 7 for high-concurrency background processing.

### 📊 Supabase Database Persistence
- Persists all webhook delivery audit records (`webhook_deliveries`).
- Tracks documentation execution runs (`runs`) with state management (`queued`, `cloning`, `diffing`, `planning`, `running_agents`, `building`, `published`, `failed`).

### 🧩 Unified Diff Parsing
- Parses raw git diff payloads into structured modification objects using `unidiff`.

---

## System Architecture

```text
+-------------------------------------------------------------------------------+
|                                GitHub Cloud                                   |
|                          (Merged Pull Request Event)                           |
+-------------------------------------------------------------------------------+
                                       |
                                       | HTTPS Webhook POST
                                       v
+-------------------------------------------------------------------------------+
|                           FastAPI Web Application                             |
|                                                                               |
|  +---------------------------+        +------------------------------------+  |
|  |     /health (GET)         |        |   /webhooks/github (POST)          |  |
|  +---------------------------+        +------------------------------------+  |
|                                                         |                     |
|                                      Signature & Filter | Validation          |
|                                                         v                     |
|                                       +------------------------------------+  |
|                                       |      WebhookService Logic          |  |
|                                       +------------------------------------+  |
+-------------------------------------------------------------------------------+
                                       |
                   +-------------------+-------------------+
                   |                                       |
                   v                                       v
+------------------------------------+   +--------------------------------------+
|       Supabase (PostgreSQL)        |   |           Redis 7 Broker             |
|                                    |   |                                      |
|  - webhook_deliveries (Idempotent) |   |  - Celery Background Queue          |
|  - runs (State & Diff Storage)     |   |                                      |
+------------------------------------+   +--------------------------------------+
                                                           |
                                                           v
                                         +--------------------------------------+
                                         |         Celery Worker Process        |
                                         |                                      |
                                         |  - Repo Clone & Checkout             |
                                         |  - Diff Parsing                      |
                                         |  - Multi-Agent Graph Routing        |
                                         |  - PR Summary Commenting             |
                                         +--------------------------------------+
```

---

## End-to-End Workflow

### 1. Webhook Receipt & Verification
1. GitHub sends a `POST` request to `/webhooks/github`.
2. FastAPI extracts `X-Hub-Signature-256` and computes expected HMAC-SHA256 signature against the raw body.
3. If signature verification fails, returns HTTP 401.

### 2. Idempotency Check & Filtering
1. `WebhookService` attempts to claim `delivery_id` in Supabase table `webhook_deliveries`. If already present, returns `status: "ignored"`, `reason: "duplicate"`.
2. Evaluates `event == "pull_request"` and `action == "closed"` with `merged == True`. Non-merge actions return HTTP 202 with `status: "ignored"`.

### 3. Run Creation & Enqueue
1. Creates a new record in Supabase `runs` table with state `queued`.
2. Enqueues the job payload into Celery (`AUTODOCS_QUEUE_BACKEND=celery`) backed by Redis.

### 4. Background Processing
1. Celery worker consumes `process_pr_event` task.
2. Clones target repository at commit SHAs (`head_sha` and `base_sha`).
3. Computes diff, executes documentation graph agents, and stores output.

---

## Technology Stack

| Layer | Technology | Description |
|---|---|---|
| **Language** | Python 3.11+ | Primary application language |
| **Web Framework** | FastAPI | High-performance async REST API framework |
| **Web Server** | Uvicorn | ASGI web server |
| **Database** | Supabase (PostgreSQL) | Managed PostgreSQL database & client SDK |
| **Queue & Broker** | Celery + Redis 7 | Asynchronous task execution & message broker |
| **Containers** | Docker Compose | Container orchestration for backing services (Redis) |
| **Diff Parser** | Unidiff | Python unified diff parsing utility |
| **Testing** | Pytest + Pytest-Asyncio | Test runner & async assertion suite |
| **Linter / Formatter** | Ruff | Ultra-fast Python linter and code formatter |
| **Tunneling** | Ngrok | Public HTTPS tunnel for local webhook testing |

---

## Project Structure

```text
Agent New/
│
├── backend/                        # FastAPI application core
│   ├── accessors/                  # Database access layer (Supabase & In-Memory)
│   │   ├── run.py                  # Documentation runs accessor
│   │   └── webhook_delivery.py     # Webhook delivery idempotency accessor
│   │
│   ├── controllers/                # FastAPI router definitions
│   │   └── github_webhook.py       # POST /webhooks/github endpoint
│   │
│   ├── infrastructure/             # Infrastructure clients & task queue interface
│   │   ├── queue.py                # Inline & Celery queue implementations
│   │   └── supabase.py             # Supabase client instantiation
│   │
│   ├── jobs/                       # Celery worker configuration & task functions
│   │   ├── celery_app.py           # Celery application instance
│   │   └── tasks.py                # Async task handlers
│   │
│   ├── models/                     # Pydantic data schemas & Enums
│   │   ├── enums.py                # RunStatus, AgentStatus, PublishedDocStatus
│   │   ├── jobs.py                 # PullRequestJob schema
│   │   ├── run.py                  # RunCreate & RunRecord models
│   │   └── webhook_delivery.py     # WebhookDelivery models
│   │
│   ├── services/                   # Business logic engine
│   │   └── webhook.py              # HMAC validation, filtering, & dispatch
│   │
│   ├── config.py                   # Pydantic Settings configuration
│   └── main.py                     # FastAPI app factory & entrypoint
│
├── builder/                        # Documentation build & graph router
├── change_graph/                   # Change graph construction utilities
├── conductor/                      # Product specification & track documentation
├── infra/                          # Infrastructure definitions
│   └── docker-compose.yml          # Redis development service configuration
│
├── repo_manager/                   # Git repository clone & checkout manager
├── tests/                          # Automated Pytest suite
│   ├── test_celery_integration.py  # Celery task queue integration tests
│   ├── test_celery_tasks.py        # Task execution logic tests
│   └── test_webhook.py             # Webhook signature & filter tests
│
├── .env.example                    # Sample environment variables configuration
├── pyproject.toml                  # Project packaging, dependencies, & tools config
└── README.md                       # Project documentation
```

---

## Core Components

### `backend/main.py`
Initializes FastAPI, configures routers, instantiates database accessors based on settings, and exposes the `/health` diagnostic route.

### `backend/services/webhook.py`
Core service for:
- `verify_signature()`: Constant-time comparison (`hmac.compare_digest`) for SHA256 signatures.
- `handle()`: Orchestrates idempotency checking, event filtering, run record insertion, and queue dispatching.

### `backend/accessors/webhook_delivery.py`
Provides `SupabaseWebhookDeliveryAccessor` to record incoming webhook deliveries. Handles Postgres unique constraint violation (`23505`) gracefully for duplicate event suppression.

### `backend/accessors/run.py`
Provides `SupabaseRunAccessor` to record job execution status in the `runs` table.

### `backend/infrastructure/queue.py`
Implements the `JobQueue` protocol:
- `InlineJobQueue`: Appends jobs in memory for dev testing.
- `CeleryQueue`: Dispatches jobs to Redis broker via `celery_app.send_task()`.

---

## Installation

### Prerequisites

- **Python 3.11+**
- **Docker Desktop** (for running Redis)
- **Git**
- **Ngrok** (optional, for receiving GitHub cloud webhooks locally)

### 1. Clone & Setup Virtual Environment

```bash
git clone https://github.com/your-username/autodocs.git
cd "Agent New"
```

Create and activate virtual environment:

**On macOS/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**On Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 2. Install Project Dependencies

Install the application in editable mode with development dependencies:

```bash
python -m pip install -e '.[dev]'
```

---

## Environment Configuration

Create a `.env` file in the root directory:

```bash
copy .env.example .env
```

Fill in your configuration variables:

```env
AUTODOCS_ENVIRONMENT=development
AUTODOCS_GITHUB_WEBHOOK_SECRET=your_github_webhook_secret_here
AUTODOCS_QUEUE_BACKEND=celery
AUTODOCS_REDIS_URL=redis://localhost:6379/0

# Supabase Credentials
SUPABASE_URL=https://your-supabase-project.supabase.co
SUPABASE_PUBLISHABLE_KEY=your_publishable_key
SUPABASE_SECRET_KEY=your_secret_key
SUPABASE_JWKS_URL=https://your-supabase-project.supabase.co/auth/v1/.well-known/jwks.json
```

---

## Docker & Services Setup

AutoDocs uses Docker Compose to run Redis for background task queuing.

Start the backing service:

```bash
docker compose -f infra/docker-compose.yml up -d
```

Verify that Redis is running:

```bash
docker compose -f infra/docker-compose.yml ps
```

Expected output:
```text
NAME            IMAGE            COMMAND                  SERVICE   STATUS
infra-redis-1   redis:7-alpine   "docker-entrypoint.s…"   redis     Up (healthy)
```

---

## Running the Application

### 1. Start the FastAPI Server

In your active Python environment:

```bash
python -m uvicorn backend.main:app --reload
```

Server will run on:
```text
http://127.0.0.1:8000
```

Interactive Swagger UI documentation will be available at:
```text
http://127.0.0.1:8000/docs
```

---

## Exposing via Ngrok

To receive live webhooks from GitHub during local development:

1. Launch Ngrok pointing to port `8000` with host header rewriting:
   ```bash
   ngrok http 127.0.0.1:8000 --url https://your-custom-domain.ngrok-free.dev --host-header=rewrite
   ```

2. Go to your GitHub Repository ➔ **Settings** ➔ **Webhooks** ➔ **Add Webhook**:
   - **Payload URL**: `https://your-custom-domain.ngrok-free.dev/webhooks/github`
   - **Content type**: `application/json`
   - **Secret**: *(Same string set in `AUTODOCS_GITHUB_WEBHOOK_SECRET` in `.env`)*
   - **Events**: Select **Pull requests**

---

## API Overview

### Health Check

```http
GET /health
```

**Response (200 OK):**
```json
{
  "status": "ok",
  "environment": "development"
}
```

### GitHub Webhook Handler

```http
POST /webhooks/github
```

**Required Headers:**
* `X-GitHub-Event`: Event type (e.g. `pull_request`, `ping`)
* `X-GitHub-Delivery`: Unique delivery UUID
* `X-Hub-Signature-256`: HMAC-SHA256 signature (`sha256=...`)

**Response (202 Accepted - Queued):**
```json
{
  "status": "queued",
  "event": "pull_request",
  "delivery_id": "67b8d1f0-96a2-11f1-9060-a8373f7fb8f6",
  "action": "closed",
  "reason": null,
  "job_id": "67b8d1f0-96a2-11f1-9060-a8373f7fb8f6"
}
```

**Response (202 Accepted - Ignored Non-Merge Event):**
```json
{
  "status": "ignored",
  "event": "pull_request",
  "delivery_id": "67b8d1f0-96a2-11f1-9060-a8373f7fb8f6",
  "action": "opened",
  "reason": "not_a_merge_event",
  "job_id": null
}
```

---

## Database & Storage Schema

### `webhook_deliveries` Table (Supabase)

| Column | Type | Description |
|---|---|---|
| `id` | UUID (PK) | Auto-generated row ID |
| `delivery_id` | Text (Unique) | GitHub `X-GitHub-Delivery` UUID header |
| `event_type` | Text | Event name (`pull_request`, `ping`) |
| `action` | Text | Sub-action (`opened`, `closed`, `reopened`) |
| `repository` | Text | Full repository name (`owner/repo`) |
| `received_at` | Timestamptz | Creation timestamp |
| `processed` | Boolean | Event processing flag |

### `runs` Table (Supabase)

| Column | Type | Description |
|---|---|---|
| `id` | UUID (PK) | Run tracking ID |
| `repository` | Text | Target GitHub repository |
| `pr_number` | Integer | Pull Request number |
| `delivery_id` | Text | Associated delivery ID |
| `head_sha` | Text | Merged commit SHA |
| `base_sha` | Text | Base target branch commit SHA |
| `status` | Text | Run status (`queued`, `cloning`, `diffing`, `planning`, `running_agents`, `building`, `published`, `failed`) |
| `raw_diff` | Text | Unified git diff string |
| `created_at` | Timestamptz | Run creation timestamp |
| `completed_at` | Timestamptz | Run completion timestamp |

---

## PR Webhook Lifecycle

```text
   Developer opens PR
           |
           v
   GitHub Webhook (`action: opened`)
           |
           v
   FastAPI receives request -> HMAC Verified
           |
           v
   Logged in `webhook_deliveries` (Supabase)
           |
           v
   Status: "ignored" (reason: "not_a_merge_event")
           |
           +---------------------------------------------+
                                                         |
                                                         v
                                            Developer Merges PR
                                                         |
                                                         v
                                            GitHub Webhook (`action: closed`, `merged: true`)
                                                         |
                                                         v
                                            FastAPI receives request -> HMAC Verified
                                                         |
                                                         v
                                            Logged in `webhook_deliveries` (Supabase)
                                                         |
                                            Logged in `runs` (Supabase, status: "queued")
                                                         |
                                            Enqueued to Celery / Redis
                                                         |
                                                         v
                                            Worker processes & updates docs 🚀
```

---

## Testing & Quality Assurance

Run the automated test suite with Pytest:

```bash
python -m pytest
```

Run code quality lint checks with Ruff:

```bash
ruff check .
```

---

## Troubleshooting

### 1. `ERR_NGROK_8012` (Bad Gateway)
* **Cause**: Ngrok was pointed to port `80` instead of port `8000`.
* **Fix**: Restart ngrok specifying port `8000`:
  ```bash
  ngrok http 127.0.0.1:8000 --url https://your-domain.ngrok-free.dev --host-header=rewrite
  ```

### 2. `ERR_NGROK_3004` (Connection Timeout)
* **Cause**: On Windows, `localhost` can resolve to IPv6 `[::1]:8000` while Uvicorn is bound to IPv4 `127.0.0.1:8000`.
* **Fix**: Use explicit `127.0.0.1:8000` with `--host-header=rewrite`.

### 3. `Multiple top-level packages discovered in a flat-layout`
* **Cause**: Setuptools error during `pip install -e .` due to multiple top-level directories.
* **Fix**: Ensure `[tool.setuptools.packages.find]` is present in [pyproject.toml](file:///d:/Projects/Agent%20New/pyproject.toml).

---

## Current Limitations

- AST parsing is currently focused on Python source repositories.
- Webhook events are scoped to merged pull request flows (`closed` with `merged: true`).
- Large repository diffs require asynchronous task workers (Celery + Redis).

---

## Planned Enhancements

1. **Celery Worker Deep Execution**: Full checkout and shallow diff parsing within worker tasks.
2. **Multi-Agent Change Graph Engine**: Routing diff chunks to specialized documentation agents based on impacted graph nodes.
3. **Automated PR Comments**: Post markdown documentation summaries directly back to GitHub PR threads before updating repository docs.
