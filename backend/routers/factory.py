from __future__ import annotations

from typing import List
from pathlib import Path
import json

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import HttpUrl

from backend.routers.services import create_service, ServiceCreate

router = APIRouter(prefix="/factory", tags=["factory"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def factory_form() -> str:
    html = """
    <html>
      <head>
        <title>0pi Bazaar Factory</title>
        <style>
          body { font-family: -apple-system, system-ui, Segoe UI, Roboto, sans-serif; margin: 24px; }
          label { display: block; margin: 8px 0 4px; }
          input, select { width: 100%; padding: 8px; }
          .row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
          .row3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
          button { margin-top: 16px; padding: 10px 16px; }
        </style>
      </head>
      <body>
        <h1>Publish an Endpoint</h1>
        <form method="post" action="/factory">
          <label>Provider Name</label>
          <input name="provider_name" required />

          <div class="row">
            <div>
              <label>CDP Wallet ID</label>
              <input name="cdp_wallet_id" required />
            </div>
            <div>
              <label>Payout Wallet (Embedded)</label>
              <input name="payout_wallet" required />
            </div>
          </div>

          <div class="row">
            <div>
              <label>API Docs URL</label>
              <input name="api_docs_url" type="url" required />
            </div>
            <div>
              <label>Upstream Endpoint URL</label>
              <input name="upstream_url" type="url" required />
            </div>
          </div>

          <div class="row3">
            <div>
              <label>HTTP Method</label>
              <select name="method">
                <option>POST</option>
                <option>GET</option>
                <option>PUT</option>
                <option>PATCH</option>
                <option>DELETE</option>
              </select>
            </div>
            <div>
              <label>Category</label>
              <input name="category" placeholder="e.g. weather, ai, data" required />
            </div>
            <div>
              <label>Price per call (USDC)</label>
              <input name="price_per_call_usdc" type="number" min="0" step="0.000001" value="0.01" required />
            </div>
          </div>

          <h3>Provider API Key (optional)</h3>
          <div class="row">
            <div>
              <label>API Key (stored encrypted)</label>
              <input name="api_key_plain" />
            </div>
            <div>
              <label>Auth Location</label>
              <select name="auth_location">
                <option>header</option>
                <option>query</option>
              </select>
            </div>
          </div>
          <div class="row">
            <div>
              <label>Auth Key (e.g., Authorization or api_key)</label>
              <input name="auth_key" value="Authorization" />
            </div>
            <div>
              <label>Auth Template (e.g., Bearer {key})</label>
              <input name="auth_template" value="Bearer {key}" />
            </div>
          </div>

          <div class="row">
            <div>
              <label>API Key Ref (optional note)</label>
              <input name="api_key_ref" placeholder="external reference or note" />
            </div>
            <div></div>
          </div>

          <button type="submit">Create Service</button>
        </form>
      </body>
    </html>
    """
    return html


@router.post("")
def factory_submit(
    provider_name: str = Form(...),
    cdp_wallet_id: str = Form(...),
    payout_wallet: str = Form(...),
    api_docs_url: str = Form(...),
    upstream_url: str = Form(...),
    method: str = Form("POST"),
    category: str = Form(...),
    price_per_call_usdc: float = Form(...),
    api_key_plain: str | None = Form(None),
    auth_location: str | None = Form("header"),
    auth_key: str | None = Form("Authorization"),
    auth_template: str | None = Form("Bearer {key}"),
    api_key_ref: str | None = Form(""),
):
    try:
        payload = ServiceCreate(
            provider_name=provider_name,
            provider_id=None,
            cdp_wallet_id=cdp_wallet_id,
            api_docs_url=HttpUrl(api_docs_url),
            upstream_url=HttpUrl(upstream_url),
            method=method,
            price_per_call_usdc=price_per_call_usdc,
            payout_wallet=payout_wallet,
            api_key_ref=api_key_ref or "",
            category=category,
            auth_location=auth_location,
            auth_key=auth_key,
            auth_template=auth_template,
            api_key_plain=api_key_plain,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid form: {e}")

    out = create_service(payload)
    # Simple post-create page
    return HTMLResponse(
        f"""
        <html><body>
          <h2>Service created</h2>
          <pre>{json.dumps(out, indent=2)}</pre>
          <p><a href="/admin/services/{out['service_id']}">View in Admin</a></p>
          <p><code>POST {out['x402_url']}</code></p>
        </body></html>
        """
    )


@router.get("/catalog", response_class=JSONResponse)
def catalog() -> List[dict]:
    # Read raw MCP listing files; when ASA library is available, transform to its schema here.
    mcp_dir = Path(__file__).resolve().parents[1] / "mcp_listings"
    items: List[dict] = []
    if mcp_dir.exists():
        for p in sorted(mcp_dir.glob("*.json")):
            try:
                items.append(json.loads(p.read_text()))
            except Exception:
                continue
    return items

