#!/usr/bin/env python3
"""
Export backend/mcp_listings/*.json into mcp_server/catalog.json
Merges existing listing fields and ensures the minimal schema for MCP tools:
[
  {
    "id": "svc_...",
    "name": "...",
    "category": "...",
    "price_per_call_usdc": 0.01,
    "docs_url": "...",
    "upstream_url": "...",   # optional; if absent, MCP server can fall back to api_base
    "method": "POST",        # optional; defaults to POST
    "x402_url": "/x402/..."
  }
]
"""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "mcp_listings"
DEST = ROOT / "mcp_server" / "catalog.json"

rows = []
if SRC.exists():
    for p in sorted(SRC.glob("*.json")):
        try:
            data = json.loads(p.read_text())
            row = {
                "id": data.get("id"),
                "name": data.get("name") or data.get("provider_name") or data.get("id"),
                "category": data.get("category", ""),
                "price_per_call_usdc": float(data.get("price_per_call_usdc", 0.0)),
                "docs_url": data.get("docs_url", ""),
                "upstream_url": data.get("upstream_url", ""),
                "method": (data.get("method") or "POST").upper(),
                "x402_url": data.get("x402_url", ""),
                # allow MCP server to fallback if upstream_url is blank
                "api_base": data.get("api_base", ""),
            }
            rows.append(row)
        except Exception:
            continue

DEST.parent.mkdir(parents=True, exist_ok=True)
DEST.write_text(json.dumps(rows, indent=2))
print(f"Wrote {len(rows)} rows to {DEST}")

