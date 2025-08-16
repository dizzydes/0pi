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
def admin_home(_: None = Depends(require_admin)) -> str:
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

    rows_html = "".join(
        f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td>"
        f"<td style='text-align:right'>{r[3]}</td>"
        f"<td><a href='/admin/services/{r[0]}'>details</a></td>"
        f"<td><a target='_blank' href='https://thegraph.com/explorer?query=0pi&service_id={r[0]}'>verify</a></td>"
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
def service_details(service_id: str, _: None = Depends(require_admin)) -> str:
    if not service_id.startswith("svc_"):
        raise HTTPException(status_code=404, detail="Unknown service_id")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.service_id, p.provider_name, s.category
        FROM services s JOIN providers p ON p.provider_id = s.provider_id
        WHERE s.service_id = ?
        """,
        (service_id,),
    )
    head = cur.fetchone()
    cur.execute(
        """
        SELECT call_id, timestamp, response_hash, tx_hash
        FROM api_calls
        WHERE service_id = ?
        ORDER BY timestamp DESC
        LIMIT 100
        """,
        (service_id,),
    )
    calls = cur.fetchall()
    cur.close()
    conn.close()

    name = head[1] if head else "unnamed"
    category = head[2] if head else ""

    rows_html = "".join(
        f"<tr>"
        f"<td>{c[0]}</td>"
        f"<td>{c[1]}</td>"
        f"<td><code>{c[2]}</code></td>"
        f"<td><a target='_blank' href='https://basescan.org/tx/{(c[3] or '').replace('0x','')}'>{c[3] or ''}</a></td>"
        f"<td><a target='_blank' href='https://thegraph.com/explorer?query=0pi&service_id={service_id}&response_hash={(c[2] or '')}'>verify</a></td>"
        f"</tr>" for c in calls
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
        </style>
      </head>
      <body>
        <h1>{service_id} — {name} <small style='color:#555'>[{category}]</small></h1>
        <p><a href="/admin">Back</a> · <a target="_blank" href="https://thegraph.com/explorer?query=0pi&service_id={service_id}">View in The Graph</a></p>
        <table>
          <thead>
            <tr><th>Call ID</th><th>When</th><th>Response Hash</th><th>Tx Hash</th><th>Verify</th></tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
      </body>
    </html>
    """
    return html

