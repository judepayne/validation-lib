# Production

This document covers what is needed to move validation-lib from its current POC state to a production deployment. Only concrete, near-term concerns are covered.

---

## Current state

The library is production-ready for single-instance, embedded deployments. The following are known gaps:

| Area | Status |
|---|---|
| Core validation logic | Complete |
| Auto-refresh / hot reload | Complete |
| JSON-RPC server | Complete |
| Two-tier configuration | Complete — supports remote URIs |
| Coordination service | **Stubbed** — returns empty data |
| Concurrency / threading | **Open design question** |
| Structured logging / metrics | Not implemented |
| Security (auth, input limits) | Not implemented |

---

## Coordination service

### Current state

`CoordinationProxy` (`coordination_proxy.py`) is a stub that always returns `{}`. Rules that declare `required_data` terms will receive an empty dict and should return `NORUN` gracefully.

### What production requires

1. **Implement the HTTP client** in `coordination_proxy.py`:
   ```python
   response = requests.post(
       f"{self.base_url}/fetch-data",
       json={"entity_type": entity_type, "entity_data": entity_data,
             "vocabulary_terms": vocabulary_terms},
       timeout=self.timeout_ms / 1000.0,
   )
   ```

2. **Enable in config** — set `enabled: true` in `coordination-service-config.yaml` and point `base_url` to the production endpoint.

3. **Add retry logic** — the config already has `retry_attempts`; implement exponential backoff in `CoordinationProxy`.

4. **Define fallback behaviour** — when the service is unavailable, rules that need required data should receive `NORUN` status. This must be tested explicitly.

5. **Remote config** — host `coordination-service-config.yaml` at a URL so all deployments share the same endpoint config:
   ```yaml
   # local-config.yaml
   coordination_service_config_uri: "https://config.example.com/coordination-service-config.yaml"
   ```

---

## Configuration in production

The library requires only one change to move from local development to production:

```yaml
# validation_lib/local-config.yaml
business_config_uri: "https://rules-cdn.example.com/prod/logic/business-config.yaml"
```

This single change causes the library to fetch the entire `logic/` package from the CDN on startup. The rules team publishes new rules to the CDN; running instances pick them up within `logic_cache_max_age_seconds` (default 30 minutes) without any service restart.

To tune the refresh window:

```yaml
logic_cache_max_age_seconds: 900   # 15 minutes — pick up rule changes faster
```

---

## Deployment patterns

### As an embedded library

The recommended pattern for most applications:

```python
# Create once at startup; reuse across the lifetime of the process
from validation_lib import ValidationService
service = ValidationService()

# Use from request handlers, batch jobs, etc.
results = service.validate("loan", loan_data, "quick")
```

`ValidationService` is not thread-safe — see [Performance and concurrency](#performance-and-concurrency) below.

### As a JSON-RPC subprocess

For non-Python host applications (Clojure, Java, Go, etc.):

```bash
python -m validation_lib.jsonrpc_server
```

The host spawns this as a persistent child process and communicates via stdin/stdout. The Python process is long-lived — logic is loaded once and cached in-process. See [JSON-RPC Server](JSONRPC-SERVER.md) for the full protocol and client examples.

### As a Docker container (JSON-RPC)

```dockerfile
FROM python:3.11-slim
RUN pip install git+https://github.com/judepayne/validation-lib.git@<sha>
CMD ["python", "-m", "validation_lib.jsonrpc_server"]
```

Pin to a specific commit SHA for reproducible builds. The `validation-service` project uses this pattern.

---

## Performance and concurrency

### Current design

`ValidationService` and `ValidationEngine` are **not thread-safe**. The engine modifies `sys.path` and `sys.modules` at initialisation and on `reload_logic()`, and the entity helper registry is a module-level singleton. Running concurrent `validate()` calls from multiple threads against a shared `ValidationService` instance is untested and likely unsafe.

### Single-threaded use

For single-threaded use — one request at a time, or sequential batch processing — the current design is correct and performant. Rule execution is CPU-bound and typically fast (sub-millisecond per rule for simple checks).

### Batch validation

`batch_validate()` and `batch_file_validate()` currently iterate entities sequentially. For large batches this is the bottleneck: a 10,000-loan batch runs all loans one after another in the same thread.

**This is an open design question.** There are several candidate approaches, each with trade-offs:

**Option A — Thread-per-entity with a shared service instance**
Simple to implement with `concurrent.futures.ThreadPoolExecutor`, but thread-safety of `ValidationService` (specifically `sys.path` mutation and the module-level `VersionRegistry` singleton) has not been analysed. Requires audit before use.

**Option B — Process pool with one service per worker**
`concurrent.futures.ProcessPoolExecutor` gives true parallelism and sidesteps thread-safety concerns (each worker has its own process and memory). Cost: each worker pays the startup cost (logic cache load, `sys.path` setup). For batches large enough, this amortises. Requires logic cache to be pre-populated before spawning workers, or each worker will fetch independently.

**Option C — Async batching with a single thread**
If the bottleneck is I/O (network fetches for coordination service, remote rule loading) rather than CPU, `asyncio` could help. Pure rule execution is CPU-bound Python and would not benefit from `asyncio` without running rules in a thread executor.

**Option D — Pre-fork the service, share nothing**
Start N worker processes each with a `ValidationService` instance, distribute batch items across them via a queue (e.g. multiprocessing.Queue or Redis). Cleanest concurrency model, highest operational complexity.

**Recommendation**: If batch throughput is a concern, Option B (process pool) is the most straightforward path that avoids thread-safety unknowns. However, this has not been implemented or benchmarked. The design should be validated with profiling before committing to an approach.

The single-threaded JSON-RPC server has the same limitation: it handles one request at a time. Horizontal scaling requires either multiple server processes behind a load balancer, or moving to an HTTP or gRPC transport (see [JSON-RPC Server — Trade-offs](JSONRPC-SERVER.md#trade-offs)).

---

## Security

**HTTPS only for remote URIs** — `local-config.yaml` and `business-config.yaml` should always use `https://` URLs in production. The library fetches and executes remote Python rule files; ensure the source is trusted and served over TLS.

**Input size limits** — `batch_file_validate()` currently reads the entire file into memory with a 50 MB cap. Very large files should be split into smaller batches by the caller.

**Remote code execution** — Rule files fetched from remote URLs are executed as Python code. The library trusts the HTTPS source. A TODO comment in `rule_fetcher.py` marks the location where hash pinning or signature verification could be added as a future hardening step.

**No authentication layer** — the JSON-RPC server has no built-in authentication. If exposed over a network (rather than over local stdio), authentication must be handled by the surrounding infrastructure.

---

## Production readiness checklist

- [x] Core validation logic
- [x] Auto-refresh (configurable TTL)
- [x] JSON-RPC server for multi-language use
- [x] Remote URI support for logic and configs
- [x] Exception chaining and descriptive error messages
- [ ] Coordination service HTTP implementation
- [ ] Thread-safety audit and concurrency design for batch operations
- [ ] Structured logging (JSON format, correlation IDs)
- [ ] Input validation and size limits beyond the current 50 MB file cap
- [ ] Authentication for JSON-RPC server if network-exposed
- [ ] Hash pinning or signature verification for remote rule files
- [ ] Load testing of batch validation at target volume
