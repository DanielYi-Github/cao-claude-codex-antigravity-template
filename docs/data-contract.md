# Data Contract

Document the shape of any data your project reads or writes here — request
or response payloads, local fixtures, database rows, or file formats that
more than one agent needs to agree on.

Keep it as a plain table so `agy_ui_data` and `codex_reviewer` can check
implementation code against it without guessing:

| Path | Type | Meaning |
|---|---|---|
| `example.field` | string | What it is and any constraints |

If your current task has no shared data shape yet, leave this file as a
stub — `scripts/verify-local.sh` only checks that it exists, not that it is
filled in. Do not put production data, credentials, or real user data in
this file; use synthetic examples only.
