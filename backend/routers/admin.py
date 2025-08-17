from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from backend.db import get_connection

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")) -> None:
    # Demo mode: no admin auth required
    return None


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
               (SELECT COUNT(1) FROM api_calls ac WHERE ac.service_id = s.service_id) AS call_count,
               COALESCE(s.price_per_call_usdc, 0) AS price_per_call_usdc
        FROM services s
        JOIN providers p ON p.provider_id = s.provider_id
        ORDER BY s.created_at
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    import os
    # Prefer Gateway explorer by subgraph ID if provided, else fall back to base+search
    _subgraph_id = os.getenv("SUBGRAPH_ID")
    _subgraph_chain = os.getenv("SUBGRAPH_CHAIN", "base")
    subgraph_explorer_full = (
        f"https://thegraph.com/explorer/subgraphs/{_subgraph_id}?view=Query"
        if _subgraph_id else None
    )
    subgraph_base = os.getenv("SUBGRAPH_EXPLORER_BASE", "https://thegraph.com/explorer?query=0pi&search=")

    def _verify_link(service_id: str) -> str:
        if subgraph_explorer_full:
            return f"<a target='_blank' href='{subgraph_explorer_full}'>verify</a>"
        return f"<a target='_blank' href='{subgraph_base}{service_id}'>verify</a>"

    rows_html = "".join(
        (
            lambda service_id, name, category, call_count, price: (
                f"<tr><td><a href='/admin/services/{name}' style='font-weight:600; color:#1d4ed8; text-decoration:none'>{name}</a></td><td>{category}</td>"
                f"<td style='text-align:right'>{call_count}</td>"
                f"<td style='text-align:right'>$ {call_count * float(price):.2f}</td>"
                f"<td>{_verify_link(service_id)}</td>"
                f"<td><form method='post' action='/admin/services/{service_id}/delete' style='display:inline'>"
                f"<button type='submit' onclick=\"return confirm('Delete this service?')\">Delete</button>"
                f"</form></td>"
                f"</tr>"
            )
        )(*r) for r in rows
    )

    html = f"""
    <html>
      <head>
        <title>0pi Admin</title>
        <style>
          body {{ font-family: -apple-system, system-ui, Segoe UI, Roboto, sans-serif; margin: 24px; }}
          table {{ border-collapse: collapse; width: 100%; }}
          th, td {{ border: 1px solid #ddd; padding: 8px; }}
          th {{ background: #f5f5f5; text-align: left; }}
        </style>
      </head>
      <body>
        <h1>Available Agentic APIs</h1>
        <a href="https://x402.gitbook.io/x402/getting-started/quickstart-for-buyers">You will pay with USDC on Base with each request using the x402 standard</a>
        <br/>
        <br/>
        <br/>
        <table>
          <thead>
            <tr><th>Name</th><th>Category</th><th>Call Count</th><th>x402 Revenue</th><th>Verify (The Graph)</th><th>Actions</th></tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
      </body>
    </html>
    """
    return html


@router.get("/services/{provider_name}", response_class=HTMLResponse)
def service_details(provider_name: str) -> str:
    conn = get_connection()
    cur = conn.cursor()
    # Resolve provider -> latest service for that provider
    cur.execute(
        """
        SELECT s.service_id, p.provider_name, s.category, s.price_per_call_usdc, s.api_docs_url, s.upstream_base_url
        FROM services s
        JOIN providers p ON p.provider_id = s.provider_id
        WHERE p.provider_name = ?
        ORDER BY s.created_at DESC
        LIMIT 1
        """,
        (provider_name,),
    )
    head = cur.fetchone()
    if not head:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Unknown provider_name")
    service_id = head[0]

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
    price_usdc = float(head[3]) if head else 0.0
    docs_url = str(head[4]) if head else "#"
    upstream_base = str(head[5]) if head else ""
    price_usdc = float(head[3]) if head else 0.0
    docs_url = str(head[4]) if head else "#"

    import os, json as _json
    import requests as _r
    # Build explorer link
    _subgraph_id = os.getenv("SUBGRAPH_ID")
    _subgraph_chain = os.getenv("SUBGRAPH_CHAIN", "base")
    subgraph_explorer_full = (
        f"https://thegraph.com/explorer/subgraphs/{_subgraph_id}?view=Query"
        if _subgraph_id else None
    )
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
            headers = {}
            api_key = os.getenv("SUBGRAPH_API_KEY")
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            gr = _r.post(subgraph_url, json=q, headers=headers, timeout=20)
            if gr.ok:
                data = gr.json().get("data", {})
                for ev in data.get("apiCalls", []) or []:
                    h = (ev.get("txHash") or "").lower()
                    if h:
                        chain_by_tx[h] = ev
                        if h.startswith("0x"):
                            chain_by_tx[h[2:]] = ev
        except Exception:
            chain_by_tx = {}

    # Build rows showing local vs on-chain
    def row(c):
      call_id, when, local_hash, txh, status_code, size_bytes = c[0], c[1], c[2], c[3], c[4], c[5]
      txh_disp_raw = txh or ""
      txh_disp = txh_disp_raw if (txh_disp_raw.startswith("0x") or txh_disp_raw == "") else ("0x" + txh_disp_raw)
      key = (txh_disp or "").lower()
      ev = chain_by_tx.get(key) or (chain_by_tx.get(key[2:]) if key.startswith("0x") else None)
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
          f"<td><a target='_blank' href='{base_scan}{txh_disp}'>{txh_disp}</a></td>"
          f"</tr>"
      )

    rows_html = "".join(row(c) for c in calls)

    # Daily usage data for chart
    days = [d[0] for d in daily]
    counts = [int(d[1]) for d in daily]
    maxc = max(counts) if counts else 1
    days_json = _json.dumps(days)
    counts_json = _json.dumps(counts)

    html = f"""
    <html>
      <head>
        <title>0pi Admin - {provider_name}</title>
        <style>
          body {{ font-family: -apple-system, system-ui, Segoe UI, Roboto, sans-serif; margin: 24px; }}
          table {{ border-collapse: collapse; width: 100%; }}
          th, td {{ border: 1px solid #ddd; padding: 8px; }}
          th {{ background: #f5f5f5; text-align: left; }}
          code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }}
          .section {{ margin: 20px 0; }}
          .page-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }}
          .chart-card {{ min-width: 420px; max-width: 480px; }}
          .chart-title {{ margin: 0 0 8px 0; color: #333; font-size: 14px; font-weight: 600; text-align: right; }}
        </style>
      </head>
      <body>
        <div class="page-header">
          <div>
            <h1>{provider_name} — {name} <small style='color:#555'>[{category}]</small><form method='post' action='/admin/services/{service_id}/delete' style='display:inline'><button type='submit' onclick="return confirm('Delete this service?')">Delete this service</button></form></h1>
            <p><a href="/admin">Back to Menu</a> · <a target="_blank" href="{(subgraph_explorer_full or (subgraph_base + str(service_id)))}">View in The Graph</a></p>

            <p style='color:#444'>
              API Base: <code>https://0pi.dev/{provider_name}/ + <a href="{docs_url}" target="_blank" rel="noopener noreferrer">endpoint</a></code><br/>
              Pricing: <strong>${price_usdc:.2f} USDC</strong> per call, paid at request-time by x402<br/>
              Upstream base: <code>{upstream_base}</code><br/>
            </p>
          </div>
          <div class="chart-card">
            <div class="chart-title">Daily usage (last 7 days)</div>
            <svg id="usage-chart" width="440" height="200" viewBox="0 0 440 200" role="img" aria-label="Daily usage line chart"></svg>
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

        <script>
          (function() {{
            const days = {days_json};
            const counts = {counts_json};
            const svg = document.getElementById('usage-chart');
            const w = 440, h = 200, padL = 36, padR = 12, padT = 12, padB = 28;
            const innerW = w - padL - padR;
            const innerH = h - padT - padB;
            const max = Math.max(1, ...(counts.length ? counts : [0]));
            const n = Math.max(1, counts.length);
            const x = i => padL + (n === 1 ? innerW/2 : (i * innerW) / (n - 1));
            const y = v => padT + (innerH - (v / max) * innerH);

            // Axes
            const axes = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            axes.setAttribute('stroke', '#ccc');
            axes.setAttribute('stroke-width', '1');
            // X axis
            const xAxis = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            xAxis.setAttribute('x1', padL); xAxis.setAttribute('y1', padT + innerH);
            xAxis.setAttribute('x2', padL + innerW); xAxis.setAttribute('y2', padT + innerH);
            axes.appendChild(xAxis);
            // Y axis
            const yAxis = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            yAxis.setAttribute('x1', padL); yAxis.setAttribute('y1', padT);
            yAxis.setAttribute('x2', padL); yAxis.setAttribute('y2', padT + innerH);
            axes.appendChild(yAxis);
            svg.appendChild(axes);

            // Y ticks (0 and max)
            const yTicks = [0, max];
            yTicks.forEach((tv) => {{
              const ty = y(tv);
              const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
              t.setAttribute('x', padL - 8);
              t.setAttribute('y', ty + 4);
              t.setAttribute('text-anchor', 'end');
              t.setAttribute('font-size', '10');
              t.setAttribute('fill', '#666');
              t.textContent = String(tv);
              svg.appendChild(t);
            }});

            // X labels (first, middle, last) to avoid clutter
            if (days.length) {{
              const labelIdx = [...new Set([0, Math.floor((days.length-1)/2), days.length-1])];
              labelIdx.forEach(i => {{
                const tx = x(i);
                const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                t.setAttribute('x', tx);
                t.setAttribute('y', padT + innerH + 16);
                t.setAttribute('text-anchor', 'middle');
                t.setAttribute('font-size', '10');
                t.setAttribute('fill', '#666');
                t.textContent = days[i];
                svg.appendChild(t);
              }});
            }}

            if (!counts.length) {{
              const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
              t.setAttribute('x', w - 8);
              t.setAttribute('y', 16);
              t.setAttribute('text-anchor', 'end');
              t.setAttribute('font-size', '12');
              t.setAttribute('fill', '#777');
              t.textContent = 'No calls in the last 7 days';
              svg.appendChild(t);
              return;
            }}

            // Line path
            const pts = counts.map((v,i) => `${'${'}x(i){'}'},${'${'}y(v){'}'}`).join(' ');
            const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
            poly.setAttribute('fill', 'none');
            poly.setAttribute('stroke', '#4f46e5');
            poly.setAttribute('stroke-width', '2');
            poly.setAttribute('points', counts.map((v,i)=>`${'${'}x(i){'}'},${'${'}y(v){'}'}`).join(' '));
            svg.appendChild(poly);

            // Dots
            counts.forEach((v,i) => {{
              const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
              c.setAttribute('cx', x(i));
              c.setAttribute('cy', y(v));
              c.setAttribute('r', '3');
              c.setAttribute('fill', '#4f46e5');
              svg.appendChild(c);
            }});
          }})();
        </script>
      </body>
    </html>
    """
    return html

@router.post("/services/{service_id}/delete")
def delete_service(service_id: str):
    if not service_id.startswith("svc_"):
        raise HTTPException(status_code=400, detail="invalid service id")
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Delete dependent rows first
        cur.execute("DELETE FROM service_secrets WHERE service_id = ?", (service_id,))
        cur.execute("DELETE FROM api_calls WHERE service_id = ?", (service_id,))
        cur.execute("DELETE FROM services WHERE service_id = ?", (service_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    # Also remove MCP listing file if present
    try:
        from pathlib import Path
        mcp_dir = Path(__file__).resolve().parents[1] / "mcp_listings"
        listing_path = mcp_dir / f"{service_id}.json"
        listing_path.unlink(missing_ok=True)
    except Exception:
        # Do not fail deletion if file removal fails
        pass
    return JSONResponse({"status": "deleted", "service_id": service_id, "mcp_listing_removed": True})

