"""Minimal CAO workflow for Claude Code + Antigravity + Codex.

The workflow deliberately uses a sequential path for the first demo. It is
simpler to observe than a parallel fan-out and makes the handoff contract
visible. Each step returns a compact result; large artifacts should be written
by the worker into the repository and referenced by path.
"""

from pathlib import Path

from cao_workflow import emit_output, get_inputs, run_step


INPUTS = {
    "project_dir": {"type": "path", "required": True},
}


inputs = get_inputs()
project_dir = Path(inputs["project_dir"])
task_file = project_dir / "docs" / "demo-task.md"
artifact_dir = project_dir / "artifacts"

lead = run_step(
    "claude_code",
    "claude_lead",
    f"""You are the lead developer for the CAO demo project.

Read {task_file} and {project_dir / 'AGENTS.md'}.
Create the initial task specification at {artifact_dir / 'spec.md'}.
Implement only a small, reviewable change based on the demo task, preferably a
minimal project-health dashboard or a clearly documented implementation plan
if this repository has no UI runtime yet. Keep all data synthetic and local.
Run ./scripts/verify-local.sh if possible. Return a concise summary, changed
paths, and the commit or working-tree state. Do not access secrets.""",
    step_id="lead-implementation",
)

ui = run_step(
    "antigravity_cli",
    "agy_ui_data",
    f"""You are the UI and data specialist.

Read {task_file}, {project_dir / 'AGENTS.md'}, and the lead result below.
Inspect the local fixture {project_dir / 'src/data/project_health.json'} and
{project_dir / 'docs/data-contract.md'}.

Produce UI/data guidance in your response using the required headings from
AGENTS.md. If the project has a suitable frontend surface, you may create a
small prototype or notes under {artifact_dir}; otherwise return an actionable
UI plan without inventing a framework. Do not connect to a production service.

Lead result:
{lead.output}""",
    step_id="ui-data-investigation",
)

review = run_step(
    "codex",
    "codex_reviewer",
    f"""You are the code reviewer and debugger.

Review the current working tree for the task in {task_file}. Read
{project_dir / 'AGENTS.md'}, the lead specification {artifact_dir / 'spec.md'}
if it exists, and the UI/data result below. Run safe local checks and inspect
all changed files. Do not modify the lead worktree and do not access secrets.
Return the exact review headings required by AGENTS.md.

Antigravity result:
{ui.output}""",
    step_id="codex-review",
)

final = run_step(
    "claude_code",
    "claude_lead",
    f"""Resume as the lead developer.

Read the current working tree, {project_dir / 'AGENTS.md'}, and the original
task {task_file}. Integrate only justified UI/data findings and address every
blocking Codex finding. Do not blindly follow suggestions that conflict with
the acceptance criteria. Run ./scripts/verify-local.sh and update
{artifact_dir / 'test-report.md'} with the observed result. Return a concise
completion report with changed paths, tests, unresolved questions, and the
final Git status.

Codex review:
{review.output}

Antigravity UI/data notes:
{ui.output}""",
    step_id="lead-integration",
)

emit_output(
    {
        "lead": lead.output,
        "ui_data": ui.output,
        "review": review.output,
        "final": final.output,
    }
)
