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
| Concurrency / threading | Complete |
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

After consideration, it was decided to limit concurrent operations to batch_validate on a pool of process workers, which is described below, principally because there was a potential race condition when multiple workers starting up with an empty cache, could all attempt to refresh it at the same time. It is easier to just spin up multiple instances of the library, each one owning it's own cache.

### Current design

`ValidationService` and `ValidationEngine` are **not thread-safe**. The engine modifies `sys.path` and `sys.modules` at initialisation and on `reload_logic()`, and the entity helper registry is a module-level singleton. Running concurrent `validate()` calls from multiple threads against a shared `ValidationService` instance is untested and likely unsafe.

### Single-threaded use

For single-threaded use — one request at a time, or sequential batch processing — the current design is correct and performant. Rule execution is CPU-bound and typically fast (sub-millisecond per rule for simple checks).

### Batch validation

`batch_validate()` and `batch_file_validate()` support parallel execution via a `ProcessPoolExecutor` worker pool. Enable it in `local-config.yaml`:

```yaml
batch_parallelism: true
batch_max_workers: 4   # omit for os.cpu_count()
```

**Design:** A persistent pool is created when `ValidationService` is initialised and kept alive across calls. Each worker process holds its own `ValidationService` instance (loaded once from the `/tmp` cache) — there is no shared state between workers. Entities are distributed across workers and results collected in input order.

**Measured performance — 200 loans, thorough ruleset, 8-core macOS (Python 3.13):**

| Config | Mean | Min | Max | Ent/sec | Speedup |
|---|---|---|---|---|---|
| Sequential (no pool) | 8,343 ms | 8,239 ms | 8,459 ms | 24 | 1.00× |
| Parallel — 2 workers | 4,204 ms | 4,124 ms | 4,259 ms | 48 | 1.98× |
| **Parallel — 4 workers** | **2,978 ms** | **2,636 ms** | **3,289 ms** | **67** | **2.80×** |
| Parallel — 8 workers | 3,450 ms | 3,358 ms | 3,621 ms | 58 | 2.42× |

4 workers is optimal on this machine. 8 workers underperforms 4 due to macOS performance/efficiency core topology and the IPC overhead of pickling results across more processes. Start at `os.cpu_count() / 2` and tune from there using `tests/bench_batch.py`.

The worker pool uses a `spawn` start method explicitly to avoid `fork`-related deadlocks in threaded host processes (e.g. the MCP server).

**Worker mode:** Workers run with `_worker_mode=True`, which disables auto-refresh. Only the main process manages cache freshness. This prevents multiple workers from simultaneously clearing and re-fetching the shared `/tmp/validation-lib/logic/` cache.

**`reload_logic()` interaction:** Calling `reload_logic()` shuts down the pool (`shutdown(wait=True)`), clears the cache, re-fetches logic, then recreates the pool with fresh workers. Because `ValidationService` is used one call at a time, the pool is always idle when `reload_logic()` is triggered, so no in-flight work is interrupted.

**Cleanup:** Call `service.close()` when done to release worker processes immediately. Without it, workers are cleaned up on garbage collection or process exit.

The JSON-RPC server inherits parallel batch performance automatically — a `batch_validate` JSON-RPC call with `batch_parallelism: true` fans out internally across workers with no changes needed on the client side.

---

## Multiple instances

### The stdio constraint

The JSON-RPC server communicates over stdin/stdout. A single server process owns one pair of streams, which means **one client per server process** — there is no mechanism for a second client to attach to an already-running server. This is a fundamental property of the stdio transport, not a bug.

For most embedded use cases this is fine: the host application spawns one server process, keeps it alive, and sends all requests through it. The long-lived process pays the startup cost (logic fetch, `sys.path` setup, cache warm) once and amortises it across all requests.

The constraint becomes significant when a host application wants to:

- **Fan out load** across multiple Python workers to increase request throughput
- **Run multiple logically independent instances** against different rule sets or logic repos simultaneously (e.g. a staging instance alongside a production instance)
- **Share a pool of validation servers** across several client processes

None of these are achievable with stdio transport alone.

### Running multiple independent instances

The practical multi-instance pattern today is to spawn N independent server processes, each with its own stdin/stdout pair, and route requests across them in the host. This is straightforward but has an important implication: **each process needs its own logic cache directory**.

The cache root defaults to `/tmp/validation-lib/` but is configurable via `logic_cache_dir` in `local-config.yaml`. If two instances run on the same machine pointing at different logic sources, give each a distinct path:

```yaml
# Instance A — production rules
logic_cache_dir: "/tmp/validation-lib-prod"
business_config_uri: "https://cdn.example.com/prod/business-config.yaml"

# Instance B — staging rules
logic_cache_dir: "/tmp/validation-lib-staging"
business_config_uri: "https://cdn.example.com/staging/business-config.yaml"
```

Instances pointing at the *same* logic source can safely share a cache dir — one fetch serves all, and the auto-refresh debounce prevents stampedes. The problem is only when logic sources differ. Without separate dirs, `reload_logic()` does `shutil.rmtree` on the whole tree — instance A reloading while instance B is mid-validation would corrupt B's imports.

### Path to socket-based transport

For higher-throughput multi-client scenarios, the right move is to replace stdio with a TCP or Unix domain socket transport. This would allow:

- Multiple clients to connect to one server
- Request multiplexing over a single long-lived connection
- Load balancing across a named pool of servers

The `ValidationService` API itself needs no changes — only the transport layer in `jsonrpc_server.py` changes. The JSON-RPC protocol is identical; only the stream source differs. This is documented as a future option in [JSON-RPC Server — Future options](JSONRPC-SERVER.md).

The stdio-vs-socket decision can be deferred until throughput requirements are measured. For most deployments, a small fixed pool of stdio-based subprocesses (managed by the host) is simpler to operate and sufficient.

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
- [x] Parallel batch validation via ProcessPoolExecutor (opt-in via `batch_parallelism` in local-config.yaml)
- [x] Configurable logic cache directory (`logic_cache_dir` in local-config.yaml) — required for safe multi-instance deployments on the same host
- [ ] Structured logging (JSON format, correlation IDs)
- [ ] Input validation and size limits beyond the current 50 MB file cap
- [ ] Authentication for JSON-RPC server if network-exposed
- [ ] Hash pinning or signature verification for remote rule files
- [ ] Load testing of batch validation at target volume
