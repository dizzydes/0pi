# query_pay.py
# Demo: pay an x402-style endpoint with USDC on Base using a MetaMask key (eth-account).
# Env:
#   ETH_PRIVATE_KEY=0x...               # demo-only!
#   DEMO_URL=http://localhost:8000/x402/openai/v1/models   # scheme optional
#   BASE_RPC=https://mainnet.base.org   # optional (defaults to Base mainnet)

import os, time, json, secrets, requests
from decimal import Decimal
from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account

# eth-account >=0.10 uses encode_typed_data; older versions used encode_structured_data
try:
    from eth_account.messages import encode_typed_data  # new
except ImportError:
    from eth_account.messages import encode_structured_data as encode_typed_data  # old

load_dotenv()

# -------- Env --------
PRIVATE_KEY  = os.getenv("ETH_PRIVATE_KEY")
X402_GET_URL = os.getenv("DEMO_URL")
BASE_RPC     = os.getenv("BASE_RPC", "https://mainnet.base.org")

if not PRIVATE_KEY or not X402_GET_URL:
    raise SystemExit("Set ETH_PRIVATE_KEY and DEMO_URL (optionally BASE_RPC).")

def normalize_url(u: str) -> str:
    return u if "://" in u else f"http://{u}"

X402_GET_URL = normalize_url(X402_GET_URL)

# -------- Chain / tokens --------
CHAIN_ID_BASE = 8453
USDC_BASE = Web3.to_checksum_address("0x833589fCD6EDb6E08f4c7C32D4f71B54bdA02913")  # native USDC on Base

ERC20_ABI = [
    {"constant": True, "inputs":[{"name":"a","type":"address"}], "name":"balanceOf",
     "outputs":[{"name":"","type":"uint256"}], "type":"function"},
    {"constant": True, "inputs":[], "name":"decimals",
     "outputs":[{"name":"","type":"uint8"}], "type":"function"},
    {"constant": False, "inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],
     "name":"transfer", "outputs":[{"name":"","type":"bool"}], "type":"function"},
    {"anonymous": False, "inputs":[
        {"indexed": True, "name": "from", "type": "address"},
        {"indexed": True, "name": "to",   "type": "address"},
        {"indexed": False,"name": "value","type": "uint256"}
    ], "name": "Transfer", "type": "event"},
]

# -------- Web3 setup --------
w3 = Web3(Web3.HTTPProvider(BASE_RPC))
if not w3.is_connected():
    raise SystemExit(f"Failed to connect to Base RPC at {BASE_RPC}")

acct = Account.from_key(PRIVATE_KEY)
addr = acct.address

usdc = w3.eth.contract(address=USDC_BASE, abi=ERC20_ABI)
USDC_DEC = usdc.functions.decimals().call()

def eth_balance(a):  return Decimal(w3.eth.get_balance(a)) / Decimal(10**18)
def usdc_balance(a): return Decimal(usdc.functions.balanceOf(a).call()) / Decimal(10**USDC_DEC)

def print_balances(tag):
    print(f"[{tag}] {addr}")
    print(f"  ETH  (Base): {eth_balance(addr):.6f}")
    print(f"  USDC (Base): {usdc_balance(addr):.6f}")

def parse_amount(price_str: str) -> Decimal:
    cleaned = "".join(ch for ch in str(price_str) if ch.isdigit() or ch == ".")
    if cleaned == "": raise ValueError(f"Unparseable price: {price_str!r}")
    return Decimal(cleaned)

def ensure_0x(txh: str) -> str:
    txh = str(txh or "")
    return txh if txh.startswith("0x") else ("0x" + txh.lstrip("0x"))

print_balances("Before")

# -------- Hit the endpoint --------
resp = requests.get(X402_GET_URL, timeout=20)
status = resp.status_code

use_eip3009 = False
pay_to_facilitator = None
amount_usdc = None
chain_id = CHAIN_ID_BASE
receiver = None

if status == 402 and ("X-402-Asset" in resp.headers or "X-402-Amount" in resp.headers):
    # Header style
    asset = (resp.headers.get("X-402-Asset") or "").upper()
    amt   = resp.headers.get("X-402-Amount")
    rcv   = resp.headers.get("X-402-Receiver")
    pay_to_facilitator = resp.headers.get("X-402-Pay-To")  # for EIP-3009 (gasless)
    chain_id = int(resp.headers.get("X-402-Chain-Id", chain_id))
    if asset != "USDC":
        raise SystemExit(f"Unsupported asset: {asset}")
    if not (amt and rcv):
        raise SystemExit("Missing X-402-Amount or X-402-Receiver.")
    amount_usdc = Decimal(amt)
    receiver = Web3.to_checksum_address(rcv)
    use_eip3009 = bool(pay_to_facilitator)
else:
    # JSON style (e.g. {"error":"payment_required","price":"$0.01","pay_to_address":"0x...","network":"base"})
    try:
        j = resp.json()
    except Exception:
        print(f"Unexpected status {status}\n{resp.text}")
        raise SystemExit("Server did not return JSON or x402 headers.")

    if (j.get("error") or "").lower() != "payment_required":
        print(json.dumps(j, indent=2))
        raise SystemExit("Expected payment_required.")

    price = j.get("price")            # e.g. "$0.01"
    rcv   = j.get("pay_to_address")   # USDC receiver
    net   = (j.get("network") or "").lower()
    if not (price and rcv):
        raise SystemExit("Missing price or pay_to_address in JSON.")

    amount_usdc = parse_amount(price)  # $0.01 → 0.01
    receiver = Web3.to_checksum_address(rcv)
    if net not in ("base", "base-mainnet"):
        raise SystemExit("Server asked for a non-Base network.")
    chain_id = CHAIN_ID_BASE
    pay_to_facilitator = None

print("x402 payment request parsed:")
print(f"  amount   : {amount_usdc} USDC")
print(f"  receiver : {receiver}")
print(f"  chain_id : {chain_id}")
print(f"  mode     : {'EIP-3009 (gasless)' if use_eip3009 else 'ERC-20 transfer (on-chain)'}")

# -------- Pay --------
ticket_id_0x = None  # will be used in receipt header (must be 0x-prefixed)

if use_eip3009:
    # --- EIP-3009 sign & POST to facilitator (gasless) ---
    valid_after  = 0
    valid_before = int(time.time() + 20*60)
    nonce_hex    = "0x" + secrets.token_bytes(32).hex()
    amount_wei   = int(amount_usdc * (10**USDC_DEC))

    domain = {"name":"USD Coin","version":"2","chainId":chain_id,"verifyingContract":USDC_BASE}
    types = {
        "EIP712Domain":[
            {"name":"name","type":"string"},{"name":"version","type":"string"},
            {"name":"chainId","type":"uint256"},{"name":"verifyingContract","type":"address"}],
        "TransferWithAuthorization":[
            {"name":"from","type":"address"},{"name":"to","type":"address"},
            {"name":"value","type":"uint256"},{"name":"validAfter","type":"uint256"},
            {"name":"validBefore","type":"uint256"},{"name":"nonce","type":"bytes32"}],
    }
    message = {"from":addr,"to":receiver,"value":amount_wei,
               "validAfter":valid_after,"validBefore":valid_before,"nonce":nonce_hex}
    structured = {"types":types,"domain":domain,"primaryType":"TransferWithAuthorization","message":message}

    try:
        signable = encode_typed_data(full_message=structured)
    except TypeError:
        try:
            signable = encode_typed_data(primitive=structured)
        except TypeError:
            signable = encode_typed_data(structured)

    signed = Account.sign_message(signable, private_key=PRIVATE_KEY)
    v = signed.v
    r_hex = "0x" + signed.r.to_bytes(32, "big").hex()
    s_hex = "0x" + signed.s.to_bytes(32, "big").hex()

    pay_payload = {
        "standard": "x402",
        "asset": "USDC",
        "chainId": chain_id,
        "method": "eip3009/transferWithAuthorization",
        "params": {**message, "v": v, "r": r_hex, "s": s_hex},
        "from": addr,
    }
    pay_resp = requests.post(pay_to_facilitator, json=pay_payload, timeout=30)
    print(f"Pay POST -> {pay_to_facilitator} | HTTP {pay_resp.status_code}")
    try:
        pj = pay_resp.json()
        print(json.dumps(pj, indent=2))
        ticket_id_0x = pj.get("ticket_id") or pj.get("txHash") or pj.get("transactionHash")
        ticket_id_0x = ensure_0x(ticket_id_0x)
    except Exception:
        print(pay_resp.text)
else:
    # --- Standard ERC-20 transfer (needs a bit of ETH for gas) ---
    amount_wei = int(amount_usdc * (10**USDC_DEC))
    tx = usdc.functions.transfer(receiver, amount_wei).build_transaction({
        "from": addr,
        "chainId": chain_id,
        "nonce": w3.eth.get_transaction_count(addr),
    })

    # Estimate gas & fee
    try:
        gas_est = w3.eth.estimate_gas(tx)
    except Exception:
        gas_est = 60_000
    try:
        priority = w3.eth.max_priority_fee
    except Exception:
        priority = int(0.1 * 1e9)  # 0.1 gwei fallback
    try:
        base_fee = w3.eth.gas_price
    except Exception:
        base_fee = int(1 * 1e9)

    tx.update({
        "gas": gas_est,
        "maxPriorityFeePerGas": priority,
        "maxFeePerGas": base_fee * 2,
    })

    signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    ticket_id_0x = ensure_0x(tx_hash.hex())
    print(f"USDC transfer sent. tx = {ticket_id_0x}")

    rcpt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    print(f"tx mined in block {rcpt.blockNumber}, status={rcpt.status}")

    # Optional: decode Transfer log for local confirmation
    try:
        transfers = usdc.events.Transfer().process_receipt(rcpt)
        if transfers:
            ev = transfers[0]["args"]
            from_, to_, val = ev["from"], ev["to"], int(ev["value"])
            amt = Decimal(val) / Decimal(10**USDC_DEC)
            print(f"Verified Transfer log: from={from_} to={to_} amount={amt} USDC")
        else:
            print("WARNING: No USDC Transfer event found in receipt.")
    except Exception as e:
        print(f"Event decode warning: {e}")

# -------- Balances after --------
print_balances("After")

# -------- Second GET with X-402-Receipt (STRICT format) --------
receipt = {
    "payer": addr,
    "amount": float(amount_usdc),  # e.g., 0.01
    "ticket_id": ticket_id_0x,     # MUST be 0x-prefixed
    "pay_to": receiver,            # provider payout wallet
    "network": "base",             # exactly "base"
}
headers = {"X-402-Receipt": json.dumps(receipt)}

print("Re-GET with receipt header:")
print(headers["X-402-Receipt"])

resp2 = requests.get(X402_GET_URL, headers=headers, timeout=20)
print(f"Re-GET -> HTTP {resp2.status_code}")
ctype = resp2.headers.get("content-type","")
if "application/json" in ctype:
    try:
        print(json.dumps(resp2.json(), indent=2))
    except Exception:
        print(resp2.text)
else:
    print(resp2.text[:1200])
