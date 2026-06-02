"""Delegate launch copy — stack-neutral verification and project cwd rules."""


def format_project_directory_block(project_dir: str) -> str:
    """CWD rules when the delegate workspace is already inside project_dir."""
    if not project_dir:
        return ""
    return (
        f"[PROJECT DIRECTORY — CRITICAL]\n"
        f"Your CWD (for bash AND file tools) is ALREADY set to {project_dir}/.\n"
        f"Do NOT `cd {project_dir}` — you are already inside it.\n"
        "- bash: run commands directly. Do NOT prefix with "
        f"`cd {project_dir} &&`.\n"
        f"- read/write/glob: paths are relative to {project_dir}/ "
        f"(e.g. write(path=\"src/main.ts\")).\n"
        f"Do NOT prepend '{project_dir}/' to paths — it will double-nest.\n"
        "NEVER create new top-level project directories."
    )


def format_delegate_verification_block() -> str:
    """Smoke-test guidance for any language / scaffold."""
    return (
        "[LOCAL VERIFICATION — any stack]\n"
        "Before task_complete, prove the deliverable works from the right directory.\n"
        "  • bash prints [CWD: ...] after every command — match it to where your files live.\n"
        "  • If run/build/import fails: cd into the folder containing YOUR files "
        "(owned_paths), then retry.\n"
        "  • Prefer the project's own scripts (npm run, make, cargo, etc.) from the "
        "manifest directory over guessing import paths.\n"
        "  • mkdir: use nested paths (src/models), not comma-separated lists that "
        "create sibling folders.\n"
        "  • File tools: one write() per path per session; use read + edit() for fixes. "
        "delete_file only when intentionally replacing from scratch."
    )
