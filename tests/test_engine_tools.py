"""Product tool stack imports."""

import os

os.environ.setdefault("NLS_PRODUCT_MODE", "1")


def test_engine_tools_importable() -> None:
    from nls.engine import tools, tool_loader, tools_builtin

    assert hasattr(tools, "ToolRegistry")
    assert hasattr(tool_loader, "load_tools_from_directory")
    assert hasattr(tools_builtin, "RequestSleepTool")
