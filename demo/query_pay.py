# pip install web3 eth-account requests python-dotenv
import os, time, json, secrets, requests
from decimal import Decimal
from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_structured_data

load_dotenv()

# ---- Config (set these in your environment) ----
PRIVATE_KEY = os.getenv("MM_PRIVATE_KEY")  # export from MetaMask (demo-only!)
X402_GET_URL = os.getenv("X402_GET_URL")  # e.g. "https://api.example.com/protected"
BASE_RPC = os.getenv("BASE_RPC", "https://mainnet.base.org")  # public Base RPC

if not PRIVATE_KEY or not X402_GET_URL:
    raise SystemExit("Set MM_PRIVATE_KEY and X402_GET_URL in your environment")

# ---- Constants ----
CHAIN_ID = 8453  # Base mainnet
USDC_ON_BASE = Web3.to_checksum_address("0x833589fCD6EDb6E08f4c7C32D4f71B54bdA02913")

# Minimal USDC ABI (balanceOf, decimals) for reads (no tx needed for EIP-3009 flow)
USDC_MIN_ABI = [
    {"constant":True,"inputs":[{"name":"a","type":"address"}],
     "name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
]

# ---- Setup ----
w3 = Web3(Web3.HTTPProvider(BASE_RPC))
acct = Account.from_key(PRIVATE_KEY)
addr = acct.address

usdc = w3.eth.contract(address=USDC_ON_BASE, abi=USDC_MIN_ABI)
usdc_dec = usdc.functions.decimals().call()

def get_eth_balance(addr):
    wei = w3.eth.get_balance(addr)
    return Decimal(wei) / Decimal(10**18)

def get_usdc_balance(addr):
    raw = usdc.functions.balanceOf(addr).call()
    return Decimal(raw) / Decimal(10**usdc_dec)

def print_balances(tag):
    eth_bal = get_eth_balance(addr)
    usdc_bal = get_usdc_balance(addr)
    print(f"[{tag}] {addr}")
    print(f"  ETH (Base):  {eth_bal:.6f}")
    print(f"  USDC (Base): {usdc_bal:.6f}")

print_balances("Before")

# ---- 1) Hit the x402-protected endpoint (expect 402 + headers) ----
r = requests.get(X402_GET_URL)
if r.status_code != 402:
    print(f"Unexpected status {r.status_code}, body:\n{r.text}")
    raise SystemExit("This demo expects 402 Payment Required.")

# Extract required info from headers (adjust names if your server differs)
def h(name, default=None): return r.headers.get(name, default)

asset = h("X-402-Asset")
if (asset or "").upper() != "USDC":
    raise SystemExit(f"Server asked for unsupported asset: {asset}")

receiver = h("X-402-Receiver")
pay_url  = h("X-402-Pay-To")
chain_id = int(h("X-402-Chain-Id", CHAIN_ID))
amount_decimal = Decimal(h("X-402-Amount"))  # e.g. "1.25" USDC

if not (receiver and pay_url):
    raise SystemExit("Missing X-402-Receiver or X-402-Pay-To headers.")

receiver = Web3.to_checksum_address(receiver)
amount_wei = int(amount_decimal * (10 ** usdc_dec))

print("x402 request:")
print(f"  amount:   {amount_decimal} USDC")
print(f"  receiver: {receiver}")
print(f"  pay_url:  {pay_url}")
print(f"  chain_id: {chain_id}")

# ---- 2) Build EIP-3009 TransferWithAuthorization for USDC ----
# USDC EIP-3009 domain is typically name: "USD Coin", version: "2"
valid_after = 0
valid_before = int(time.time() + 60 * 20)  # 20 minutes window
nonce_bytes = secrets.token_bytes(32)
nonce_hex = "0x" + nonce_bytes.hex()

domain = {
    "name": "USD Coin",
    "version": "2",
    "chainId": chain_id,
    "verifyingContract": USDC_ON_BASE,
}

types = {
    "EIP712Domain": [
        {"name":"name","type":"string"},
        {"name":"version","type":"string"},
        {"name":"chainId","type":"uint256"},
        {"name":"verifyingContract","type":"address"},
    ],
    "TransferWithAuthorization": [
        {"name":"from","type":"address"},
        {"name":"to","type":"address"},
        {"name":"value","type":"uint256"},
        {"name":"validAfter","type":"uint256"},
        {"name":"validBefore","type":"uint256"},
        {"name":"nonce","type":"bytes32"},
    ],
}

message = {
    "from": addr,
    "to": receiver,
    "value": amount_wei,
    "validAfter": valid_after,
    "validBefore": valid_before,
    "nonce": nonce_hex,
}

structured = {
    "types": types,
    "domain": domain,
    "primaryType": "TransferWithAuthorization",
    "message": message,
}

# Sign the typed data with eth-account
eip712_msg = encode_structured_data(primitive=structured)
signed = Account.sign_message(eip712_msg, private_key=PRIVATE_KEY)
v, r, s = signed.v, "0x"+signed.r.to_bytes(32, 'big').hex(), "0x"+signed.s.to_bytes(32, 'big').hex()

print("Signed EIP-3009 authorization.")
print(f"  v: {v}, r: {r[:10]}..., s: {s[:10]}...")

# ---- 3) POST to the facilitator's pay endpoint ----
# Most x402 servers accept a JSON body like below. If yours differs, match its spec.
payload = {
    "standard": "x402",
    "asset": "USDC",
    "chainId": chain_id,
    "method": "eip3009/transferWithAuthorization",
    "params": {
        **message,
        "v": v,
        "r": r,
        "s": s
    },
    # Optional: echo your address for server-side checks
    "from": addr,
}

resp = requests.post(pay_url, json=payload, timeout=30)
print(f"Pay response: HTTP {resp.status_code}")
try:
    print(resp.json())
except Exception:
    print(resp.text)

# ---- 4) Print balances again ----
print_balances("After")
