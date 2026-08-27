"""High-level task envelopes shared by CLI and remote MCP clients."""

import re
import secrets


def task_slug(value: str, fallback: str = "work") -> str:
    """Return a short routing-safe label for task ids and project streams."""
    if not isinstance(value, str):
        raise ValueError("task labels must be strings")
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug[:40].rstrip("_") or fallback


def task_handoff(correlation_id: str, agent: str) -> str:
    """Render the portable one-line wake-up prompt for a stored task."""
    return (
        f"Open MemPalace task {correlation_id} as {agent}. "
        "Claim it, follow its exact definition of done, and deliver through the logstream."
    )


def create_task(
    logstream,
    *,
    project: str,
    from_agent: str,
    to_agent: str,
    goal: str,
    branch: str,
    base_commit: str,
    done: str,
) -> dict:
    """Append one canonical task request and return it with its handoff line."""
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("task goal must not be empty")
    if not isinstance(done, str) or not done.strip():
        raise ValueError("task definition of done must not be empty")
    correlation_id = f"task_{task_slug(goal)}_{secrets.token_hex(4)}"
    body = (
        f"Goal:\n{goal}\n\n"
        f"Definition of done:\n{done}\n\n"
        "Delivery:\n"
        "Close the loop through MemPalace: claim the request, then submit a patch "
        "with mempalace_patch_submit or reply with blocked/failed evidence."
    )
    event = logstream.append_event(
        type="task.request",
        stream=f"project/{task_slug(project, fallback='project')}",
        room="delegation",
        from_agent=from_agent,
        to_agent=to_agent,
        correlation_id=correlation_id,
        branch=branch,
        base_commit=base_commit,
        status="open",
        body=body,
    )
    return {"task": event, "handoff": task_handoff(correlation_id, to_agent)}
