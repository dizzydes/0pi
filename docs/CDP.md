# CDP and x402 environment setup

Set the following environment variables for the backend to enable CDP wallet discovery/creation and prepare for x402 settlement.

Required for CDP API calls:
- CDP_KEY_ID: Key name/ID for the CDP server key
- CDP_KEY_SECRET: Private key for signing Bearer JWTs (PEM, base64 DER PKCS#8, or base64 32-byte Ed25519 seed)
- CDP_NETWORK: base-mainnet | base-sepolia (defaults inferred from X402_NETWORK/X402_CHAIN if unset)

Wallet creation (server-initiated):
- WALLET_AUTH_JWT: Wallet auth JWT for POST /cdp/wallets

Optional:
- CDP_API_HOST: default api.cdp.coinbase.com
- X402_NETWORK or X402_CHAIN: network hint for x402 headers (defaults to base)

Optional server-side x402 verification (development only):
- X402_SERVER_VERIFY=true — enable the server to attempt verification
- X402_ACCEPT_UNSIGNED=true — accept a plain JSON x-402-receipt header (development only)

Notes
- If server-side verification is disabled (default), the /x402 routes return HTTP 402 with X-402-* headers that x402-aware clients can satisfy automatically.
- On success, the service emits on-chain proofs (see backend/routers/x402.py emit_onchain_proof) and logs to the local DB.

