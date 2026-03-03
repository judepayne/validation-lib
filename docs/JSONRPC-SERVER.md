# JSON-RPC Server

validation-lib includes a JSON-RPC 2.0 server that exposes the full `ValidationService` API over stdin/stdout. This allows any language that can spawn a subprocess to use the library without a Python runtime dependency in the host application.

---

## Starting the server

### stdio (default)

```bash
python -m validation_lib.jsonrpc_server
python -m validation_lib.jsonrpc_server --debug    # log to stderr
```

The server reads newline-delimited JSON-RPC requests from stdin and writes responses to stdout. It runs until it receives EOF on stdin, SIGTERM, or SIGINT.

### TCP socket

```bash
python -m validation_lib.jsonrpc_server --port 5000
python -m validation_lib.jsonrpc_server --port 5000 --host 0.0.0.0   # accept remote connections
python -m validation_lib.jsonrpc_server --port 5000 --debug
```

The server binds a TCP socket and accepts one connection at a time. When the current client disconnects, the next connection is accepted. The JSON-RPC protocol is identical to stdio — newline-delimited JSON over the socket stream.

`--host` defaults to `127.0.0.1` (loopback only). Exposing on `0.0.0.0` is an explicit opt-in; ensure authentication is handled by the surrounding infrastructure if doing so (see [Security](PRODUCTION.md)).

#### Running multiple instances

Each server process must be configured with a **distinct `logic_cache_dir`** in `local-config.yaml`. If multiple instances share the same cache directory, a `reload_logic()` call on one process will corrupt the cache for all others. The port number is a convenient differentiator for the path:

```yaml
# local-config.yaml for the instance on port 5001
logic_cache_dir: "/tmp/validation-lib-5001"

# local-config.yaml for the instance on port 5002
logic_cache_dir: "/tmp/validation-lib-5002"
```

The mapping between port and cache path is entirely the client's choice — the library does not derive it automatically.

### Stopping the server

| Signal / event | Behaviour |
|---|---|
| **EOF on stdin** | Immediate clean shutdown |
| **SIGTERM / SIGINT** | Graceful stop after the current request completes |
| **Ctrl+C** | Same as SIGINT |

From a client process:
```python
server.stdin.close()   # triggers EOF → shutdown
server.wait()
```

---

## Protocol

JSON-RPC 2.0 over stdin/stdout with newline-delimited messages.

### Request format

```json
{"jsonrpc": "2.0", "id": 1, "method": "validate", "params": {...}}
```

`id` may be an integer, string, or `null`. Requests without an `id` field are treated as **notifications** and receive no response (per JSON-RPC 2.0 spec).

### Success response

```json
{"jsonrpc": "2.0", "id": 1, "result": ...}
```

### Error response

```json
{"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "..."}}
```

### Error codes

| Code | Constant | Meaning |
|---|---|---|
| `-32700` | `ERROR_PARSE` | Invalid JSON in request |
| `-32600` | `ERROR_INVALID_REQUEST` | Malformed JSON-RPC structure |
| `-32601` | `ERROR_METHOD_NOT_FOUND` | Unknown method name |
| `-32602` | `ERROR_INVALID_PARAMS` | Missing or invalid parameters |
| `-32000` | `ERROR_INTERNAL` | Application-level error |

---

## Methods

All `ValidationService` public methods are available:

### `validate`

```json
{
  "jsonrpc": "2.0", "id": 1,
  "method": "validate",
  "params": {
    "entity_type": "loan",
    "entity_data": {"$schema": "https://...", "id": "LOAN-001", ...},
    "ruleset_name": "quick"
  }
}
```

### `batch_validate`

```json
{
  "jsonrpc": "2.0", "id": 2,
  "method": "batch_validate",
  "params": {
    "entities": [{"$schema": "...", ...}, ...],
    "id_fields": ["id"],
    "ruleset_name": "quick"
  }
}
```

### `batch_file_validate`

```json
{
  "jsonrpc": "2.0", "id": 3,
  "method": "batch_file_validate",
  "params": {
    "file_uri": "file:///data/loans.json",
    "entity_types": ["loan"],
    "id_fields": ["id"],
    "ruleset_name": "thorough"
  }
}
```

### `discover_rules`

```json
{
  "jsonrpc": "2.0", "id": 4,
  "method": "discover_rules",
  "params": {
    "entity_type": "loan",
    "entity_data": {"$schema": "https://..."},
    "ruleset_name": "quick"
  }
}
```

### `discover_rulesets`

```json
{"jsonrpc": "2.0", "id": 5, "method": "discover_rulesets", "params": {}}
```

### `reload_logic`

```json
{"jsonrpc": "2.0", "id": 6, "method": "reload_logic", "params": {}}
```

Returns `{"status": "ok", "message": "Logic reloaded successfully"}`.

### `get_cache_age`

```json
{"jsonrpc": "2.0", "id": 7, "method": "get_cache_age", "params": {}}
```

Returns `{"cache_age": 1234.5}` (seconds, or `null` if no cache exists).

---

## Client examples

### Python — stdio

```python
import json
import subprocess

# Start the server as a subprocess
server = subprocess.Popen(
    ["python", "-m", "validation_lib.jsonrpc_server"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
)

def call(method, params):
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    server.stdin.write(json.dumps(request) + "\n")
    server.stdin.flush()
    return json.loads(server.stdout.readline())

# Discover available rulesets
response = call("discover_rulesets", {})
print(response["result"])

# Validate a loan
response = call("validate", {
    "entity_type": "loan",
    "entity_data": {
        "$schema": "https://raw.githubusercontent.com/judepayne/validation-logic/main/models/loan.schema.v1.0.0.json",
        "id": "LOAN-001",
        "loan_number": "LN-2024-001",
        "financial": {"principal_amount": 500000, "outstanding_balance": 300000,
                      "currency": "USD", "interest_rate": 0.05, "interest_type": "fixed"},
        "dates": {"origination_date": "2024-01-15", "maturity_date": "2029-01-15"},
        "status": "active",
    },
    "ruleset_name": "quick",
})
for result in response["result"]:
    print(f"{result['rule_id']}: {result['status']}")

# Shutdown
server.stdin.close()
server.wait()
```

### Python — TCP

```python
import json
import socket

# Connect to a running TCP server (started with --port 5000)
sock = socket.create_connection(("127.0.0.1", 5000))
rfile = sock.makefile("r", encoding="utf-8")
wfile = sock.makefile("w", encoding="utf-8")

def call(method, params):
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    wfile.write(json.dumps(request) + "\n")
    wfile.flush()
    return json.loads(rfile.readline())

# Discover available rulesets
print(call("discover_rulesets", {}))

# Validate a loan
response = call("validate", {
    "entity_type": "loan",
    "entity_data": {
        "$schema": "https://raw.githubusercontent.com/judepayne/validation-logic/main/models/loan.schema.v1.0.0.json",
        "id": "LOAN-001",
        "loan_number": "LN-2024-001",
        "financial": {"principal_amount": 500000, "currency": "USD",
                      "interest_rate": 0.05, "interest_type": "fixed"},
        "dates": {"origination_date": "2024-01-15", "maturity_date": "2029-01-15"},
        "status": "active",
    },
    "ruleset_name": "quick",
})
for result in response["result"]:
    print(f"{result['rule_id']}: {result['status']}")

# Close the connection
sock.close()
```

### Clojure — stdio

```clojure
(ns myapp.validation
  (:require [cheshire.core :as json])
  (:import [java.io BufferedReader BufferedWriter InputStreamReader OutputStreamWriter]))

(defn start-server []
  (let [pb (ProcessBuilder. ["python3" "-m" "validation_lib.jsonrpc_server"])
        process (.start pb)
        writer (BufferedWriter. (OutputStreamWriter. (.getOutputStream process)))
        reader (BufferedReader. (InputStreamReader. (.getInputStream process)))]
    {:process process :writer writer :reader reader}))

(defn call [{:keys [writer reader]} method params]
  (let [request (json/generate-string {:jsonrpc "2.0" :id 1 :method method :params params})]
    (.write writer request)
    (.newLine writer)
    (.flush writer)
    (json/parse-string (.readLine reader) true)))

(defn stop-server [{:keys [process writer]}]
  (.close writer)
  (.waitFor process))

;; Usage
(let [server (start-server)]
  (println (call server "discover_rulesets" {}))
  (stop-server server))
```

### Clojure — TCP

```clojure
(ns myapp.validation
  (:require [cheshire.core :as json])
  (:import [java.io BufferedReader BufferedWriter InputStreamReader OutputStreamWriter]
           [java.net Socket]))

(defn connect [host port]
  (let [socket (Socket. host port)
        writer (BufferedWriter. (OutputStreamWriter. (.getOutputStream socket) "UTF-8"))
        reader (BufferedReader. (InputStreamReader. (.getInputStream socket) "UTF-8"))]
    {:socket socket :writer writer :reader reader}))

(defn call [{:keys [writer reader]} method params]
  (let [request (json/generate-string {:jsonrpc "2.0" :id 1 :method method :params params})]
    (.write writer request)
    (.newLine writer)
    (.flush writer)
    (json/parse-string (.readLine reader) true)))

(defn disconnect [{:keys [socket]}]
  (.close socket))

;; Usage — server must already be running with --port 5000
(let [conn (connect "127.0.0.1" 5000)]
  (println (call conn "discover_rulesets" {}))
  (disconnect conn))
```

---

## Design rationale

### Why JSON-RPC 2.0 over stdio?

The JSON-RPC 2.0 over stdin/stdout design was chosen over alternatives (HTTP server, gRPC, babashka pods) for these reasons:

**Simplicity**: The entire transport layer is ~60 lines using only the Python standard library (`json`, `sys`, `signal`). No external dependencies, no port management, no service discovery.

**Native float support**: An earlier prototype used babashka pods with bencode encoding. Bencode cannot represent floating-point numbers, which meant lossy round-tripping of interest rates and ratios — a fundamental problem for a financial validation library. JSON handles floats natively.

**Single encoding layer**: The babashka pods approach required bencode wrapping a JSON payload — two encoding layers to maintain and debug. JSON-RPC uses only JSON end-to-end.

**Process reuse and caching**: The Python process is long-lived — it starts once, loads the logic cache, and serves all requests from the same process. This means the logic package is parsed and `sys.path` is configured once. An HTTP-server-per-request approach would pay this startup cost on every call.

**No port management**: stdin/stdout requires no network configuration, firewall rules, or port allocation. It works identically in local development, CI, and Docker.

**Debuggability**: You can test the server with `echo` or `cat` from the shell, or inspect request/response pairs with `--debug` logging to stderr.

### Trade-offs

The stdio transport runs a **single Python worker per host process**, which is sufficient for most embedded use cases. For horizontal scaling, use the TCP transport and run a pool of N independent server processes on distinct ports, each with its own `logic_cache_dir`. The TCP server accepts one connection at a time per process — this keeps `ValidationService` access single-threaded and safe, while N-process pools deliver N-way parallelism without shared state.

### Concurrency model (TCP)

The TCP server is deliberately sequential: it accepts one connection, serves all requests on that connection until the client disconnects, then accepts the next. This avoids all threading hazards — `ValidationService` is not thread-safe (`sys.path`, `sys.modules`, and the entity helper registry are global mutable state). For parallel throughput, run multiple server processes rather than threading a single one.
