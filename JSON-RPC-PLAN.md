# JSON-RPC Server Implementation Plan

## Overview

Create a JSON-RPC 2.0 server that wraps the ValidationService API, enabling validation-lib-py to be used from **any programming language** (Clojure, Java, Node.js, Go, etc.).

## Architecture

```
┌─────────────────────────────────┐
│   Client (Any Language)         │
│   - Clojure                     │
│   - Java                        │
│   - Node.js                     │
│   - Go, Rust, etc.              │
└─────────────┬───────────────────┘
              │
              │ JSON-RPC 2.0 over stdio
              │ (newline-delimited JSON)
              │
┌─────────────▼───────────────────┐
│   jsonrpc_server.py             │
│   - Request parsing             │
│   - Method dispatch             │
│   - Error handling              │
│   - Response formatting         │
└─────────────┬───────────────────┘
              │
              │ Direct Python calls
              │
┌─────────────▼───────────────────┐
│   ValidationService             │
│   - Pure Python API             │
└─────────────────────────────────┘
```

## JSON-RPC 2.0 Protocol

### Request Format
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "validate",
  "params": {
    "entity_type": "loan",
    "entity_data": {...},
    "ruleset_name": "quick"
  }
}
```

### Success Response Format
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [...]
}
```

### Error Response Format
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32000,
    "message": "Error description",
    "data": "Optional stack trace"
  }
}
```

## File Structure

```
validation_lib/
├── jsonrpc_server.py       # NEW: Main JSON-RPC server
└── api.py                  # Existing: ValidationService
```

## Implementation Details

### 1. JSON-RPC Server Class

```python
class ValidationJsonRpcServer:
    """JSON-RPC 2.0 server for ValidationService"""

    def __init__(self):
        self.service = ValidationService()
        self.running = False

    def start(self):
        """Start the JSON-RPC server loop"""

    def stop(self):
        """Stop the server gracefully"""

    def handle_request(self, request_json):
        """Parse request and dispatch to appropriate method"""

    def _dispatch(self, method_name, params):
        """Dispatch to ValidationService method"""
```

### 2. Supported JSON-RPC Methods

Map ValidationService methods to JSON-RPC methods:

| JSON-RPC Method | ValidationService Method | Parameters |
|----------------|--------------------------|------------|
| `validate` | `service.validate()` | entity_type, entity_data, ruleset_name |
| `discover_rules` | `service.discover_rules()` | entity_type, entity_data, ruleset_name |
| `discover_rulesets` | `service.discover_rulesets()` | (none) |
| `batch_validate` | `service.batch_validate()` | entities, id_fields, ruleset_name |
| `batch_file_validate` | `service.batch_file_validate()` | file_uri, entity_types, id_fields, ruleset_name |
| `reload_logic` | `service.reload_logic()` | (none) |
| `get_cache_age` | `service.get_cache_age()` | (none) |

### 3. Error Handling Strategy

**All exceptions caught and returned as JSON-RPC errors:**

```python
try:
    result = self._dispatch(method, params)
    return success_response(request_id, result)
except ValidationError as e:
    return error_response(request_id, -32001, f"Validation error: {e}")
except ValueError as e:
    return error_response(request_id, -32602, f"Invalid params: {e}")
except Exception as e:
    return error_response(request_id, -32000, f"Internal error: {e}")
```

**Error codes:**
- `-32700`: Parse error (invalid JSON)
- `-32600`: Invalid request (malformed JSON-RPC)
- `-32601`: Method not found
- `-32602`: Invalid params
- `-32000`: Internal application error (catch-all)
- `-32001`: Validation-specific error

### 4. Transport Layer

**stdin/stdout transport (like original):**
- Read requests from `sys.stdin` (newline-delimited JSON)
- Write responses to `sys.stdout` (newline-delimited JSON)
- Flush after each write
- Handle EOF gracefully

**Benefits:**
- Language-agnostic
- Simple protocol
- No network configuration
- Process-based isolation

### 5. Server Lifecycle

```python
def start():
    """
    Start the JSON-RPC server loop.

    - Initialize ValidationService
    - Enter request loop
    - Read from stdin
    - Process request
    - Write to stdout
    - Repeat until EOF or stop signal
    """

def stop():
    """
    Stop the server gracefully.

    - Set running flag to False
    - Clean up resources
    - Exit loop on next iteration
    """
```

### 6. Request Processing Flow

```
1. Read line from stdin
   ↓
2. Parse JSON
   ↓
3. Validate JSON-RPC format
   ↓
4. Extract method & params
   ↓
5. Dispatch to ValidationService
   ↓
6. Catch any exceptions
   ↓
7. Format response (success or error)
   ↓
8. Write to stdout
   ↓
9. Flush
   ↓
10. Loop back to step 1
```

## Implementation Steps

### Phase 1: Core Server
1. Create `jsonrpc_server.py`
2. Implement `ValidationJsonRpcServer` class
3. Implement request parsing
4. Implement response formatting
5. Implement error handling

### Phase 2: Method Dispatch
1. Create method dispatch table
2. Implement each method wrapper
3. Handle parameter validation
4. Handle type conversions

### Phase 3: Server Loop
1. Implement `start()` method
2. Implement `stop()` method
3. Implement main request loop
4. Handle EOF and interrupts
5. Add logging (optional)

### Phase 4: CLI Entry Point
1. Create `__main__.py` for `python -m validation_lib.jsonrpc_server`
2. Add command-line argument parsing
3. Add help text
4. Handle signals (SIGTERM, SIGINT)

### Phase 5: Testing
1. Create `tests/test_jsonrpc_server.py`
2. Test each JSON-RPC method
3. Test error handling
4. Test edge cases (malformed requests, EOF, etc.)

### Phase 6: Documentation
1. Add JSON-RPC section to README
2. Document protocol
3. Provide client examples (Python, Clojure)
4. Document error codes

## Usage Examples

### Starting the Server

```bash
# As a module
python -m validation_lib.jsonrpc_server

# Or directly
python validation_lib/jsonrpc_server.py
```

### Client Example (Python)

```python
import json
import subprocess

# Start the server
server = subprocess.Popen(
    ["python", "-m", "validation_lib.jsonrpc_server"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True
)

# Send request
request = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "discover_rulesets",
    "params": {}
}
server.stdin.write(json.dumps(request) + "\n")
server.stdin.flush()

# Read response
response = json.loads(server.stdout.readline())
print(response["result"])
```

### Client Example (Clojure)

```clojure
(require '[clojure.java.shell :as shell]
         '[cheshire.core :as json])

(defn call-rpc [method params]
  (let [process (.. (ProcessBuilder. ["python" "-m" "validation_lib.jsonrpc_server"])
                    (start))
        request (json/generate-string
                  {:jsonrpc "2.0"
                   :id 1
                   :method method
                   :params params})]
    ;; Write request
    (.write (.getOutputStream process) (.getBytes (str request "\n")))
    (.flush (.getOutputStream process))

    ;; Read response
    (-> (.getInputStream process)
        (slurp)
        (json/parse-string true))))

(call-rpc "discover_rulesets" {})
```

## README Documentation

New section to add:

```markdown
## JSON-RPC Server (Multi-Language Support)

validation-lib-py can be used from **any programming language** via JSON-RPC 2.0 over stdin/stdout.

### Starting the Server

```bash
python -m validation_lib.jsonrpc_server
```

### Supported Methods

All ValidationService methods are available:
- `validate` - Validate single entity
- `discover_rules` - Discover available rules
- `discover_rulesets` - Discover all rulesets
- `batch_validate` - Validate multiple entities
- `batch_file_validate` - Validate from file
- `reload_logic` - Hot reload business logic
- `get_cache_age` - Get cache age in seconds

### Protocol

JSON-RPC 2.0 over stdio (newline-delimited JSON)

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "validate",
  "params": {
    "entity_type": "loan",
    "entity_data": {...},
    "ruleset_name": "quick"
  }
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [...]
}
```

See [JSON-RPC Examples](examples/jsonrpc/) for client examples in various languages.
```

## Testing Strategy

### Unit Tests
- Test request parsing
- Test response formatting
- Test error handling
- Test method dispatch

### Integration Tests
- Test full request/response cycle
- Test each API method via JSON-RPC
- Test error scenarios
- Test EOF handling

### Client Tests
- Test from Python subprocess
- Test malformed requests
- Test concurrent requests (if supported)

## Benefits

1. **Language-agnostic** - Use from Clojure, Java, Node.js, Go, Rust, etc.
2. **Simple protocol** - Standard JSON-RPC 2.0
3. **Process isolation** - Each client gets isolated server process
4. **No dependencies** - Only Python standard library for transport
5. **Familiar pattern** - Same as original validation-lib architecture
6. **Backward compatible** - Can replace old Clojure+Python setup

## Future Enhancements (Optional)

1. **TCP socket transport** - Network-based alternative to stdio
2. **Async support** - Handle multiple requests concurrently
3. **Batch requests** - JSON-RPC batch request support
4. **Notifications** - One-way messages (no response expected)
5. **Authentication** - Token-based auth for remote access
6. **Rate limiting** - Protect against abuse
7. **Metrics** - Request counts, latency tracking

## Questions for User

1. Should we support batch JSON-RPC requests (multiple requests in one message)?
2. Do we need TCP socket support, or is stdio sufficient?
3. Should we add request logging (debug mode)?
4. Any specific error codes needed beyond the standard ones?
5. Should we create example clients in other languages?

---

**Estimated Implementation Time:** 2-3 hours
**Complexity:** Medium
**Impact:** High (enables multi-language usage)
