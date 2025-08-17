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
            :root{
              --bg:#f6f8fb;
              --card:#ffffff;
              --text:#1f2937;
              --muted:#6b7280;
              --link:#1a73e8;
              --link-hover:#1558bf;
              --border:#e5e7eb;
              --row-hover:#f1f5ff;
              --primary:#1a73e8;
              --primary-pressed:#1558bf;
              --shadow:0 1px 2px rgba(16,24,40,.06), 0 8px 24px rgba(16,24,40,.06);
            }

            body{
              font:14px/1.5 -apple-system,system-ui,Segoe UI,Roboto,"Helvetica Neue",Arial,sans-serif;
              background:var(--bg);
              color:var(--text);
              margin:24px;
            }
            h1{ font-size:22px; font-weight:600; margin-bottom:16px; }
            h3{ font-size:16px; margin:20px 0 8px; font-weight:600; color:#111827; }
            a{ color:var(--link); text-decoration:none; }
            a:hover{ color:var(--link-hover); text-decoration:underline; }

            form{
              background:var(--card);
              border:1px solid var(--border);
              border-radius:12px;
              padding:24px;
              box-shadow:var(--shadow);
              max-width:880px;
            }

            label{ display:block; margin:8px 0 4px; font-weight:500; }
            input, select{
              width:100%;
              padding:10px 12px;
              border:1px solid var(--border);
              border-radius:8px;
              font-size:14px;
              transition:border-color .15s ease, box-shadow .15s ease;
            }
            input:focus, select:focus{
              outline:none;
              border-color:var(--primary);
              box-shadow:0 0 0 3px rgba(26,115,232,0.2);
            }

            .row{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }
            .row3{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; }

            .note{
              color:var(--muted);
              font-size:13px;
              margin:6px 0 12px;
            }

            button.primary{
              margin-top:20px;
              padding:10px 18px;
              background:var(--primary);
              border:1px solid var(--primary);
              color:#fff;
              font-weight:600;
              border-radius:8px;
              cursor:pointer;
              transition:background .15s ease;
              width:auto;
            }
            button.primary:hover{ background:var(--primary-pressed); }
          </style>
        </head>
        <body>
          <h1>Publish a Service</h1>
          <form method="post" action="/factory" id="service-form">
            <label>Provider Name (lowercase, one word with underscores)</label>
            <input name="provider_name" required placeholder="e.g., openai or weather_api" />

            <h3>Payout via ENS</h3>
            <div class="note">
              We pay your service via the Ethereum address that your ENS name resolves to. 
              If you don't have one, <a href="https://app.ens.domains" target="_blank" rel="noopener noreferrer">claim an ENS name</a> first.
            </div>
            <label>ENS Name (e.g., myname.eth)</label>
            <input name="ens_name" id="ens_name" required placeholder="yourname.eth" />

            <div class="row">
              <div>
                <label>API Docs Link</label>
                <input name="api_docs_url" type="url" required />
              </div>
              <div>
                <label>Upstream Base URL</label>
                <input name="upstream_base_url" type="url" placeholder="https://api.openai.com" required />
              </div>
            </div>

            <div class="row3">
              <div></div>
              <div>
                <label>Category</label>
                <input name="category" list="categories" placeholder="Select a category" required />
                <datalist id="categories">
                  <option value="🌍 Geo & Mapping"></option>
                  <option value="⛅ Weather & Environment"></option>
                  <option value="🚖 Transportation"></option>
                  <option value="💬 Language & Chat AI"></option>
                  <option value="👁️ Vision AI"></option>
                  <option value="🎙️ Speech AI"></option>
                  <option value="🎯 Recommendation & Personalization"></option>
                  <option value="🔍 Search"></option>
                  <option value="💳 Finance & Payments"></option>
                  <option value="🛒 E-commerce"></option>
                  <option value="📊 CRM & Sales"></option>
                  <option value="📋 Project Management"></option>
                  <option value="💬 Messaging"></option>
                  <option value="✉️ Email"></option>
                  <option value="📱 Social Media"></option>
                  <option value="📰 News & Content"></option>
                  <option value="📚 Knowledge Bases"></option>
                  <option value="📈 Market Data"></option>
                  <option value="☁️ Cloud & Storage"></option>
                  <option value="🔐 Authentication & Identity"></option>
                  <option value="🛠️ DevOps & Monitoring"></option>
                  <option value="🌐 Translation"></option>
                  <option value="📆 Calendar & Scheduling"></option>
                  <option value="📑 Document & Data Management"></option>
                </datalist>
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

            <button class="primary" type="submit">Create Service</button>
          </form>
          <script>
            const form = document.getElementById('service-form');
            form.addEventListener('submit', (e) => {
              const v = (document.getElementById('ens_name').value || '').trim();
              if (!/^[a-z0-9-_.]+\\.(eth|xyz|limo)$/i.test(v)) {
                e.preventDefault();
                alert('Please enter a valid ENS name, e.g., yourname.eth');
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
    ens_name: str = Form(...),
    api_docs_url: str = Form(...),
    upstream_base_url: str = Form(...),
    category: str = Form(...),
    price_per_call_usdc: float = Form(...),
    api_key_plain: str | None = Form(None),
    auth_location: str | None = Form("header"),
    auth_key: str | None = Form("Authorization"),
    auth_template: str | None = Form("Bearer {key}"),
):
    # Resolve ENS to an EVM address (Ethereum mainnet)
    import os
    from web3 import Web3
    from ens import ENS

    rpc = os.getenv("ETHEREUM_RPC_URL")
    if not rpc:
        raise HTTPException(status_code=500, detail="Missing ETHEREUM_RPC_URL for ENS resolution")
    try:
        w3 = Web3(Web3.HTTPProvider(rpc))
        ns = ENS.from_web3(w3)
        addr = ns.address(ens_name.strip())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"ENS resolution failed: {e}")

    if not addr or not Web3.is_address(addr):
        raise HTTPException(status_code=400, detail="ENS name did not resolve to a valid address")

    # Normalize to checksum
    payout_wallet = Web3.to_checksum_address(addr)

    try:
        payload = ServiceCreate(
            provider_name=provider_name,
            provider_id=None,
            cdp_wallet_id=None,
            api_docs_url=HttpUrl(api_docs_url),
            price_per_call_usdc=price_per_call_usdc,
            payout_wallet=payout_wallet,
            category=category,
            upstream_base_url=HttpUrl(upstream_base_url),
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
          <p>Payout: <code>{payout_wallet}</code> (resolved from <code>{ens_name}</code>)</p>
          <p><a href="/admin/services/{provider_name}">View in Admin</a></p>
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

