"""Exact installed SDK configuration, checked by mypy against SDK 1.0.34."""

from copilot.session import MCPServerConfig, MCPStdioServerConfig


def configuration(uvx: str) -> dict[str, MCPServerConfig]:
    blender: MCPStdioServerConfig = {
        "type": "stdio",
        "command": uvx,
        "args": [
            "--python",
            "3.11",
            "mcp-for-blender==2.0.0",
            "--host",
            "127.0.0.1",
            "--port",
            "9877",
        ],
        "tools": ["*"],
        "timeout": 30000,
    }
    return {"blender": blender}
