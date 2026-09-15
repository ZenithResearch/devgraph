# Remote access status

This branch contains no remote-access transport or deployment. The bounded
Python client accepts an injected synchronous HTTP transport and is proven
against the in-process FastAPI stack. The local monitor binds only when an
operator explicitly runs Uvicorn and its credential is a read-only synthetic
fixture.

There is no Matrix, Nostr, secS, Dregg, VPN, tunnel, relay, or public endpoint
implementation in this repository.

## Verification

```bash
uv run pytest tests/client tests/integration -q
```
