# QE Agent

An autonomous quality engineering pipeline powered by [Claude Opus 4.6](https://anthropic.com). Monitors product signals (PRs, CI events, releases), generates and runs tests, analyzes failures, self-heals flaky tests, and files Jira bugs — all without human intervention.

## Architecture

```
Product Signals
┌─────────────────────────────────────────────┐
│ Slack | Jira | PRs | CI events | Releases   │
└───────────────┬─────────────────────────────┘
                │
                ▼
         Event Streaming Layer
           (Webhook / Kafka / PubSub)
                │
                ▼
        QE Agent Orchestrator
          (7-stage pipeline)
                │
      ┌─────────┼──────────┬───────────┐
      ▼         ▼          ▼           ▼
  Context    Impact      Test       Script
  Agent      Analysis    Agent      Agent
                │
                ▼
       Sandbox Test Generation
                │
                ▼
        Parallel Test Execution
           (Local / Device Farm)
                │
                ▼
        Failure Analysis Layer
                │
       ┌────────┴────────┐
       ▼                 ▼
 Self-Healing        Bug Filing
 Test Agent          Agent
       │                 │
       ▼                 ▼
   Git Commit        Jira Ticket
```

## Pipeline Stages

| # | Stage | Agent | What it does |
|---|---|---|---|
| 1 | Context Analysis | `ContextAgent` | Fetches PR diff, changed files, linked Jira ticket |
| 2 | Impact Analysis | `ImpactAnalysisAgent` | Maps blast radius, coverage gaps, assigns risk level |
| 3 | Test Generation | `TestGenerationAgent` | Writes runnable pytest test cases for changed code |
| 4 | Test Execution | — | Runs tests locally (sandbox) or on a device farm grid |
| 5 | Failure Analysis | `FailureAnalysisAgent` | Classifies failures: bug / flaky / infra / environment |
| 6 | Self-Healing | `SelfHealingAgent` | Patches flaky and broken tests, optionally commits the fix |
| 7 | Bug Filing | `BugFilingAgent` | Deduplicates and files Jira tickets for confirmed product bugs |

## Quickstart

```bash
git clone https://github.com/vish1977/qe-agent
cd qe-agent
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### Run in demo mode (no integrations needed)

```bash
python -m src.main --demo
```

### Process a specific PR

```bash
python -m src.main --pr https://github.com/owner/repo/pull/42
```

### Start the webhook server

```bash
python -m src.main --serve
```

### Start with an ngrok tunnel (local development)

```bash
python -m src.main --serve --ngrok
```

The tunnel output prints the exact GitHub webhook URL and configuration steps.

### Replay a webhook payload from a file

```bash
python -m src.main --webhook tests/fixtures/github_pr_opened.json --source github
```

## Configuration

Copy `.env.example` to `.env` and fill in the values you need:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# GitHub (for PR context fetching and self-healing commits)
GITHUB_TOKEN=ghp_...
GITHUB_WEBHOOK_SECRET=your-webhook-secret   # set this in GitHub repo Settings → Webhooks

# Jira (for ticket context and bug filing)
JIRA_BASE_URL=https://yourorg.atlassian.net
JIRA_EMAIL=you@yourorg.com
JIRA_API_TOKEN=...
JIRA_PROJECT_KEY=QE

# Webhook server
WEBHOOK_PORT=8080

# ngrok (https://dashboard.ngrok.com)
NGROK_AUTH_TOKEN=...
NGROK_DOMAIN=               # optional: reserved domain (paid plan)
```

## GitHub Webhook Setup

1. Go to your repo → **Settings → Webhooks → Add webhook**
2. **Payload URL**: `https://<your-host>/webhook/github`
3. **Content type**: `application/json`
4. **Secret**: value from `GITHUB_WEBHOOK_SECRET`
5. **Events**: Pull requests, Check runs, Workflow runs

## Project Structure

```
qe-agent/
├── src/
│   ├── agents/
│   │   ├── base.py           # Shared agentic loop (Claude API + tool use + streaming)
│   │   ├── context.py        # PR / commit / Jira context
│   │   ├── impact.py         # Blast radius and coverage gap analysis
│   │   ├── testgen.py        # Test case generation
│   │   ├── failure.py        # Failure classification
│   │   ├── healing.py        # Self-healing test patches
│   │   └── bugfiling.py      # Jira bug creation
│   ├── tools/
│   │   ├── github.py         # GitHub API tools
│   │   ├── jira.py           # Jira API tools
│   │   ├── testing.py        # Local runner + device farm
│   │   └── code.py           # File read/write, AST analysis
│   ├── streaming/
│   │   ├── consumer.py       # In-process event queue + Kafka consumer
│   │   ├── webhook_server.py # aiohttp server (HMAC verification, SSE stream)
│   │   └── ngrok_tunnel.py   # ngrok tunnel for local development
│   ├── orchestrator.py       # 7-stage pipeline state machine
│   ├── models.py             # Pydantic models for all pipeline data
│   ├── config.py             # Environment-based configuration
│   └── main.py               # CLI entry point
└── tests/
    ├── fixtures/             # Sample GitHub webhook payloads
    └── test_webhook.py       # Webhook server tests (8 cases)
```

## Running Tests

```bash
pytest tests/test_webhook.py -v
```

```
PASSED  test_health_endpoint
PASSED  test_pr_opened_valid_signature
PASSED  test_pr_opened_invalid_signature
PASSED  test_pr_opened_missing_signature
PASSED  test_check_run_failed_event
PASSED  test_unsupported_github_event
PASSED  test_malformed_json
PASSED  test_no_secret_skips_verification
```

## Tech Stack

- **AI**: [Claude Opus 4.6](https://anthropic.com) with adaptive thinking and streaming
- **HTTP server**: [aiohttp](https://docs.aiohttp.org)
- **Data models**: [Pydantic v2](https://docs.pydantic.dev)
- **Tunnel**: [pyngrok](https://pyngrok.readthedocs.io)
- **Console output**: [Rich](https://rich.readthedocs.io)
