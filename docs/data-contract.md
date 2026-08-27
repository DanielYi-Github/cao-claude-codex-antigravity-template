# Project Health Data Contract

The demo uses `src/data/project_health.json` as synthetic local data. It is intentionally small so the three-agent workflow can inspect it without a database connection.

| Path | Type | Meaning |
|---|---|---|
| `project.id` | string | Stable project identifier |
| `project.name` | string | Human-readable project name |
| `project.health_score` | integer, 0–100 | Aggregate health score |
| `project.updated_at` | ISO 8601 string | Last update timestamp |
| `checks` | array | Recent health checks |
| `checks[].id` | string | Stable check identifier |
| `checks[].label` | string | Display label |
| `checks[].status` | `passing` / `warning` / `failing` | Check state |
| `checks[].duration_ms` | non-negative integer | Duration of the check |

A production implementation should validate this contract at the application boundary and treat unknown status values as an error or an explicit fallback state. The demo does not connect to a live database and contains no personal or production data.
