"""
ZeroMQ port allocation for DayTradingBotV2 -- see PLAN.md's "Inter-process
transport" section. All sockets bind to 127.0.0.1 only (no external exposure).

    Signal service:              SIGNAL_PORT (fixed)
    Sizing,     account index i: SIZING_PORT(i)     = base + i*10
    Execution,  account index i: EXECUTION_PORT(i)  = base + i*10 + 1

Adding a new account just needs a new index -- no manual port bookkeeping
beyond adding that account to ACCOUNTS below.
"""

SIGNAL_PORT = 5556
ACCOUNT_BASE_PORT = 5560
ACCOUNT_PORT_STRIDE = 10

# Registered accounts, in allocation order. Index in this list IS the account's
# port-allocation index -- append-only, never reorder or reuse a freed slot,
# or a stale/cached publisher address could resolve to a different account.
ACCOUNTS = [
    'start200',
    # '10k',  # add once Stage 2 brings the second account online
]


def account_index(slug: str) -> int:
    return ACCOUNTS.index(slug)


def sizing_port(slug: str) -> int:
    return ACCOUNT_BASE_PORT + account_index(slug) * ACCOUNT_PORT_STRIDE


def execution_port(slug: str) -> int:
    return sizing_port(slug) + 1


def signal_endpoint() -> str:
    return f'tcp://127.0.0.1:{SIGNAL_PORT}'


def sizing_endpoint(slug: str) -> str:
    return f'tcp://127.0.0.1:{sizing_port(slug)}'


def execution_endpoint(slug: str) -> str:
    return f'tcp://127.0.0.1:{execution_port(slug)}'
