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
        <h1>Publish a Service</h1>
        <form method="post" action="/factory" id="service-form">
          <input type="hidden" name="cdp_wallet_id" id="cdp_wallet_id" />
          <input type="hidden" name="payout_wallet" id="payout_wallet" />
          <label>Provider Name (lowercase, one word with underscores)</label>
          <input name="provider_name" required placeholder="e.g., openai or weather_api" />

          <div class="row">
            <div>
              <label>CDP Wallet ID (optional)</label>
              <input name="cdp_wallet_id_visible" id="cdp_wallet_id_visible" placeholder="Enter CDP wallet/account id (optional)" />
            </div>
            <div>
              <label>Payout Wallet (Base EVM address)</label>
              <input name="payout_wallet_visible" id="payout_wallet_visible" placeholder="0x... (auto-filled or enter manually)" />
            </div>
          </div>

          <div class="row">
            <div>
              <label>API Docs Link</label>
              <input name="api_docs_url" type="url" required />
            </div>
            <div></div>
          </div>

          <div class="row3">
            <div></div>
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
        <script>
          // Sync manual CDP wallet id entry (optional) to hidden field
          document.getElementById('cdp_wallet_id_visible').addEventListener('input', (e) => {
            document.getElementById('cdp_wallet_id').value = e.target.value;
          });
          // Sync manual payout wallet entry to hidden field
          document.getElementById('payout_wallet_visible').addEventListener('input', (e) => {
            document.getElementById('payout_wallet').value = e.target.value;
          });
        </script>
        <script>
          // Basic EVM address validation for payout wallet (0x + 40 hex)
          const payoutInput = document.getElementById('payout_wallet_visible');
          const form = document.getElementById('service-form');
          function isEvm(addr){ return /^0x[0-9a-fA-F]{40}$/.test(addr||''); }
          form.addEventListener('submit', (e) => {
            const v = (payoutInput.value||'').trim();
            if(!isEvm(v)){
              e.preventDefault();
              alert('Payout Wallet must be a valid EVM address (0x + 40 hex chars)');
            }
          });
        </script>
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
          <p>API Base: <code>{out.get('api_base','')}</code></p>
          <p>X402: <code>POST {out['x402_url']}</code></p>
          <p><a href="/admin/services/{provider_name}">View in Admin</a></p
          <h3>Try It</h3>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;max-width:900px">
            <div>
              <label>Method</label>
              <select id="try-method">
                <option>GET</option>
                <option>POST</option>
                <option>PUT</option>
                <option>PATCH</option>
                <option>DELETE</option>
              </select>
            </div>
            <div>
              <label>Path (relative to {out.get('api_base','')})</label>
              <input id="try-path" placeholder="v1/endpoint" />
            </div>
            <div style="grid-column: span 2">
              <label>X-Target-Url (optional, full URL)</label>
              <input id="try-target" placeholder="https://api.example.com/v1/endpoint" />
            </div>
            <div style="grid-column: span 2">
              <label>Headers (JSON)</label>
              <textarea id="try-headers" rows="4" placeholder="{{\"Content-Type\": \"application/json\"}}"></textarea>
            </div>
            <div style="grid-column: span 2">
              <label>Body</label>
              <textarea id="try-body" rows="6" placeholder="{{ }}"></textarea>
            </div>
            <div style="grid-column: span 2">
              <button id="try-send">Send</button>
            </div>
            <div style="grid-column: span 2">
              <label>Response</label>
              <textarea id="try-out" rows="10" readonly></textarea>
            </div>
          </div>
          <script>
            const base = '{out.get('api_base','')}';
            document.getElementById('try-send').addEventListener('click', async (e) => {{
              e.preventDefault();
              const m = document.getElementById('try-method').value;
              const p = document.getElementById('try-path').value.trim().replace(/^\/+/, '');
              const t = document.getElementById('try-target').value.trim();
              let headers = {{}};
              try {{ headers = JSON.parse(document.getElementById('try-headers').value || '{{}}'); }} catch {{}}
              const bodyTxt = document.getElementById('try-body').value;
              const opts = {{ method: m, headers }};
              if (m !== 'GET' && m !== 'HEAD' && bodyTxt) opts.body = bodyTxt;
              const url = (base || '') + (p ? '/' + p : '');
              if (t) headers['X-Target-Url'] = t;
              const r = await fetch(url, opts);
              const txt = await r.text();
              document.getElementById('try-out').value = `Status: ${{r.status}}\n` + txt;
            }});
          </script>
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

