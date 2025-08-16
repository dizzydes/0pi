from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse

from backend.db import get_connection

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")) -> None:
    expected = os.getenv("ADMIN_TOKEN")
    if not expected:
        # Missing admin token configuration
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not configured in environment")
    if x_admin_token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def admin_home() -> str:
    # Aggregate calls by service
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.service_id,
               COALESCE(p.provider_name, 'unnamed') AS name,
               COALESCE(s.category, '') AS category,
               (SELECT COUNT(1) FROM api_calls ac WHERE ac.service_id = s.service_id) AS call_count
        FROM services s
        JOIN providers p ON p.provider_id = s.provider_id
        ORDER BY s.created_at
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    import os
    subgraph_base = os.getenv("SUBGRAPH_EXPLORER_BASE", "https://thegraph.com/explorer?query=0pi&search=")
    rows_html = "".join(
        f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td>"
        f"<td style='text-align:right'>{r[3]}</td>"
        f"<td><a href='/admin/services/{r[0]}'>details</a></td>"
        f"<td><a target='_blank' href='{subgraph_base}{r[0]}'>verify</a></td>"
        f"</tr>" for r in rows
    )

    html = f"""
    <html>
      <head>
        <title>0pi Admin - Analytics</title>
        <style>
          body {{ font-family: -apple-system, system-ui, Segoe UI, Roboto, sans-serif; margin: 24px; }}
          table {{ border-collapse: collapse; width: 100%; }}
          th, td {{ border: 1px solid #ddd; padding: 8px; }}
          th {{ background: #f5f5f5; text-align: left; }}
        </style>
      </head>
      <body>
        <h1>0pi Analytics</h1>
        <table>
          <thead>
            <tr><th>Service ID</th><th>Name</th><th>Category</th><th>Call Count</th><th>Details</th><th>Verify (The Graph)</th></tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
      </body>
    </html>
    """
    return html


@router.get("/services/{service_id}", response_class=HTMLResponse)
def service_details(service_id: str) -> str:
    if not service_id.startswith("svc_"):
        raise HTTPException(status_code=404, detail="Unknown service_id")

    conn = get_connection()
    cur = conn.cursor()
    # Head info
    cur.execute(
        """
        SELECT s.service_id, p.provider_name, s.category
        FROM services s JOIN providers p ON p.provider_id = s.provider_id
        WHERE s.service_id = ?
        """,
        (service_id,),
    )
    head = cur.fetchone()

    # Recent calls (local)
    cur.execute(
        """
        SELECT call_id, timestamp, response_hash, tx_hash, status_code, response_size_bytes
        FROM api_calls
        WHERE service_id = ?
        ORDER BY timestamp DESC
        LIMIT 200
        """,
        (service_id,),
    )
    calls = cur.fetchall()

    # Daily usage last 7 days (local)
    cur.execute(
        """
        SELECT substr(timestamp, 1, 10) AS day, COUNT(1)
        FROM api_calls
        WHERE service_id = ? AND timestamp >= date('now','-7 day')
        GROUP BY day
        ORDER BY day
        """,
        (service_id,),
    )
    daily = cur.fetchall()
    cur.close()
    conn.close()

    name = head[1] if head else "unnamed"
    category = head[2] if head else ""

    import os, json as _json
    import requests as _r
    subgraph_base = os.getenv("SUBGRAPH_EXPLORER_BASE", "https://thegraph.com/explorer?query=0pi&search=")
    base_scan = os.getenv("BASE_SCAN_BASE", "https://basescan.org/tx/")

    # Query subgraph for recent events and index by txHash (lowercased)
    subgraph_url = os.getenv("SUBGRAPH_URL")
    chain_by_tx: dict[str, dict] = {}
    if subgraph_url and calls:
        try:
            q = {
                "query": """
                    query($first: Int!) {
                      apiCalls(first: $first, orderBy: timestamp, orderDirection: desc) {
                        callId
                        requestHash
                        responseHash
                        txHash
                        blockNumber
                        timestamp
                      }
                    }
                """,
                "variables": {"first": 500},
            }
            gr = _r.post(subgraph_url, json=q, timeout=20)
            if gr.ok:
                data = gr.json().get("data", {})
                for ev in data.get("apiCalls", []) or []:
                    h = (ev.get("txHash") or "").lower()
                    if h:
                        chain_by_tx[h] = ev
        except Exception:
            chain_by_tx = {}

    # Build rows showing local vs on-chain
    def row(c):
      call_id, when, local_hash, txh, status_code, size_bytes = c[0], c[1], c[2], c[3], c[4], c[5]
      txh_disp = txh or ""
      ev = chain_by_tx.get((txh or "").lower()) if txh else None
      on_hash = (ev or {}).get("responseHash", "")
      match = (local_hash == on_hash and local_hash != "")
      match_icon = "✅" if match else ("⚠️" if ev else "—")
      return (
          f"<tr>"
          f"<td>{call_id}</td>"
          f"<td>{when}</td>"
          f"<td><code>{local_hash}</code></td>"
          f"<td><code>{on_hash}</code></td>"
          f"<td style='text-align:center'>{match_icon}</td>"
          f"<td>{status_code}</td>"
          f"<td>{size_bytes}</td>"
          f"<td><a target='_blank' href='{base_scan}{txh_disp.replace('0x','')}'>{txh_disp}</a></td>"
          f"</tr>"
      )

    rows_html = "".join(row(c) for c in calls)

    # Daily usage mini-graph (simple bar chart using divs)
    days = [d[0] for d in daily]
    counts = [int(d[1]) for d in daily]
    maxc = max(counts) if counts else 1
    bars = "".join(
        f"<div style='display:flex;align-items:center;gap:8px'>"
        f"<div style='width:80px;color:#555'>{day}</div>"
        f"<div style='background:#4f46e5;height:12px;width:{int(300*cnt/maxc)}px;border-radius:3px'></div>"
        f"<div style='min-width:24px;text-align:right'>{cnt}</div>"
        f"</div>" for day, cnt in zip(days, counts)
    )

    html = f"""
    <html>
      <head>
        <title>0pi Admin - {service_id}</title>
        <style>
          body {{ font-family: -apple-system, system-ui, Segoe UI, Roboto, sans-serif; margin: 24px; }}
          table {{ border-collapse: collapse; width: 100%; }}
          th, td {{ border: 1px solid #ddd; padding: 8px; }}
          th {{ background: #f5f5f5; text-align: left; }}
          code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }}
          .section {{ margin: 20px 0; }}
        </style>
      </head>
      <body>
        <h1>{service_id} — {name} <small style='color:#555'>[{category}]</small></h1>
        <p><a href="/admin">Back</a> · <a target="_blank" href="{subgraph_base}">View in The Graph</a></p>

        <div class="section">
          <h2>Daily usage (last 7 days)</h2>
          <div>
            {bars or '<div style="color:#777">No calls in the last 7 days</div>'}
          </div>
        </div>

        <div class="section">
          <h2>Recent calls — Local vs On-chain</h2>
          <table>
            <thead>
              <tr><th>Call ID</th><th>When</th><th>Local Response Hash</th><th>On-chain Hash</th><th>Match</th><th>Status</th><th>Size (bytes)</th><th>Tx</th></tr>
            </thead>
            <tbody>
              {rows_html}
            </tbody>
          </table>
        </div>
      </body>
    </html>
    """
    return html

