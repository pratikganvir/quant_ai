"""Shim: daily endpoints live in ``mcp_servers.quant_monthly`` (one port, one MCP)."""

from __future__ import annotations

import sys
import warnings

warnings.warn(
    "mcp_servers.quant_daily is deprecated; use: python -m mcp_servers.quant_monthly",
    DeprecationWarning,
    stacklevel=1,
)

from mcp_servers.quant_monthly.__main__ import main

if __name__ == "__main__":
    main()