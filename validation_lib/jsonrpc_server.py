#!/usr/bin/env python3
"""
JSON-RPC 2.0 Server for ValidationService

Provides a JSON-RPC interface to validation-lib, enabling usage from any
programming language that can spawn a process and communicate via stdin/stdout.

Protocol: JSON-RPC 2.0 over stdin/stdout (newline-delimited JSON)
Specification: https://www.jsonrpc.org/specification

Usage:
    python -m validation_lib.jsonrpc_server [--debug]

Example request (stdin):
    {"jsonrpc":"2.0","id":1,"method":"discover_rulesets","params":{}}

Example response (stdout):
    {"jsonrpc":"2.0","id":1,"result":{"quick":{...},"thorough":{...}}}
"""

import socket
import sys
import json
import signal
import argparse
from typing import Any, Dict, Optional, Tuple

from validation_lib import ValidationService


class ValidationJsonRpcServer:
    """JSON-RPC 2.0 server wrapping ValidationService API."""

    # JSON-RPC error codes
    ERROR_PARSE = -32700  # Invalid JSON
    ERROR_INVALID_REQUEST = -32600  # Invalid JSON-RPC structure
    ERROR_METHOD_NOT_FOUND = -32601  # Unknown method
    ERROR_INVALID_PARAMS = -32602  # Invalid parameters
    ERROR_INTERNAL = -32000  # Application error (catch-all)
    ERROR_VALIDATION = -32001  # Validation-specific error

    def __init__(self, debug: bool = False):
        """
        Initialize JSON-RPC server.

        Args:
            debug: Enable debug logging to stderr
        """
        self.service = ValidationService()
        self.running = False
        self.debug = debug

        # Method dispatch table
        self.methods = {
            "validate": self._handle_validate,
            "discover_rules": self._handle_discover_rules,
            "discover_rulesets": self._handle_discover_rulesets,
            "batch_validate": self._handle_batch_validate,
            "batch_file_validate": self._handle_batch_file_validate,
            "reload_logic": self._handle_reload_logic,
            "get_cache_age": self._handle_get_cache_age,
        }

    def _log(self, message: str):
        """Log debug message to stderr (doesn't interfere with JSON-RPC on stdout)."""
        if self.debug:
            sys.stderr.write(f"[DEBUG] {message}\n")
            sys.stderr.flush()

    def _serve_stream(self, rfile, wfile) -> None:
        """
        Serve JSON-RPC requests over any pair of file-like objects.

        Core request loop shared by stdio and TCP transports. Reads
        newline-delimited JSON requests from rfile and writes responses
        to wfile until EOF or stop signal.

        Args:
            rfile: Readable file-like object (supports readline()).
            wfile: Writable file-like object (supports write() and flush()).
        """
        while self.running:
            try:
                line = rfile.readline()

                if not line:
                    self._log("EOF received, closing connection")
                    break

                self._log("Received request")

                response = self.handle_request(line)

                # JSON-RPC 2.0: notifications (no "id") must not receive a response
                if response is not None:
                    self._send_response(response, wfile)

            except KeyboardInterrupt:
                self._log("KeyboardInterrupt received, shutting down")
                break

            except Exception as e:
                self._log(f"Fatal error in serve loop: {e}")
                import traceback

                traceback.print_exc(file=sys.stderr)
                break

    def start_stdio_server(self) -> None:
        """
        Start the JSON-RPC server reading from stdin and writing to stdout.

        Runs until EOF on stdin or a stop signal is received.
        """
        self.running = True
        self._log("ValidationService JSON-RPC server started (stdio)")
        self._serve_stream(sys.stdin, sys.stdout)
        self._log("Server stopped")

    def start_server(self) -> None:
        """Backward-compatible alias for start_stdio_server()."""
        self.start_stdio_server()

    def start_tcp_server(self, host: str, port: int) -> None:
        """
        Start the JSON-RPC server listening for TCP connections on host:port.

        Accepts one connection at a time (sequential). The next connection
        is accepted only after the current client disconnects. This keeps
        ValidationService access single-threaded and avoids any shared-state
        hazards from concurrent reload_logic() calls.

        For parallel throughput, run N independent server processes on
        distinct ports, each with its own logic_cache_dir in local-config.yaml.
        Do not share a cache directory across processes — a reload_logic()
        call on one process will corrupt the cache for all others.

        Args:
            host: Bind address. Defaults to 127.0.0.1 (loopback only).
                  Pass 0.0.0.0 to accept remote connections (ensure auth
                  is handled by surrounding infrastructure).
            port: TCP port to listen on.
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((host, port))
            srv.listen(1)
            srv.settimeout(1.0)  # allows stop_server() to take effect within 1 s
            self.running = True
            self._log(f"Listening on {host}:{port}")
            while self.running:
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue  # re-check self.running
                except OSError:
                    break  # socket closed or unrecoverable error
                with conn:
                    self._log(f"Connection from {addr}")
                    rfile = conn.makefile("r", encoding="utf-8")
                    wfile = conn.makefile("w", encoding="utf-8")
                    self._serve_stream(rfile, wfile)
                    self._log(f"Connection closed: {addr}")
        self._log("TCP server stopped")

    def stop_server(self) -> None:
        """
        Stop the server gracefully.

        Sets running flag to False, causing the active serve loop to exit
        after the current request completes.
        """
        self.running = False
        self._log("Stop signal received")

    def handle_request(self, request_json: str) -> Dict[str, Any]:
        """
        Parse and process a JSON-RPC request.

        Args:
            request_json: JSON-RPC request string

        Returns:
            JSON-RPC response dict (success or error)
        """
        request_id = None

        try:
            # Parse JSON
            try:
                request = json.loads(request_json)
            except json.JSONDecodeError as e:
                return self._error_response(None, self.ERROR_PARSE, f"Parse error: {e}")

            # Validate JSON-RPC structure
            if not isinstance(request, dict):
                return self._error_response(
                    None, self.ERROR_INVALID_REQUEST, "Request must be a JSON object"
                )

            if request.get("jsonrpc") != "2.0":
                return self._error_response(
                    None,
                    self.ERROR_INVALID_REQUEST,
                    f"Invalid JSON-RPC version: {request.get('jsonrpc')}",
                )

            request_id = request.get("id")
            is_notification = "id" not in request
            method = request.get("method")
            params = request.get("params", {})

            if not method:
                return self._error_response(
                    request_id, self.ERROR_INVALID_REQUEST, "Missing 'method' field"
                )

            if not isinstance(params, dict):
                return self._error_response(
                    request_id,
                    self.ERROR_INVALID_PARAMS,
                    f"Params must be an object, got {type(params).__name__}",
                )

            # Dispatch to method handler
            self._log(f"Dispatching method: {method}")
            result = self._dispatch(method, params)

            # Notifications must not produce a response
            if is_notification:
                return None

            return self._success_response(request_id, result)

        except KeyError as e:
            return self._error_response(request_id, self.ERROR_METHOD_NOT_FOUND, str(e))
        except Exception as e:
            # Catch any unexpected errors
            self._log(f"Error processing request: {e}")
            return self._error_response(
                request_id, self.ERROR_INTERNAL, f"Internal error: {e}"
            )

    def _dispatch(self, method: str, params: Dict[str, Any]) -> Any:
        """
        Dispatch request to appropriate ValidationService method.

        Args:
            method: JSON-RPC method name
            params: Method parameters

        Returns:
            Method result

        Raises:
            ValueError: If method not found or params invalid
        """
        if method not in self.methods:
            raise KeyError(f"Method not found: {method}")

        handler = self.methods[method]
        return handler(params)

    # Method handlers - wrap ValidationService API

    def _handle_validate(self, params: Dict[str, Any]) -> Any:
        """Handle 'validate' method."""
        entity_type = params.get("entity_type")
        entity_data = params.get("entity_data")
        ruleset_name = params.get("ruleset_name")

        if entity_type is None:
            raise ValueError("Missing required parameter: entity_type")
        if entity_data is None:
            raise ValueError("Missing required parameter: entity_data")
        if ruleset_name is None:
            raise ValueError("Missing required parameter: ruleset_name")

        return self.service.validate(entity_type, entity_data, ruleset_name)

    def _handle_discover_rules(self, params: Dict[str, Any]) -> Any:
        """Handle 'discover_rules' method."""
        entity_type = params.get("entity_type")
        entity_data = params.get("entity_data")
        ruleset_name = params.get("ruleset_name")

        if entity_type is None:
            raise ValueError("Missing required parameter: entity_type")
        if entity_data is None:
            raise ValueError("Missing required parameter: entity_data")
        if ruleset_name is None:
            raise ValueError("Missing required parameter: ruleset_name")

        return self.service.discover_rules(entity_type, entity_data, ruleset_name)

    def _handle_discover_rulesets(self, params: Dict[str, Any]) -> Any:
        """Handle 'discover_rulesets' method."""
        # No parameters required
        return self.service.discover_rulesets()

    def _handle_batch_validate(self, params: Dict[str, Any]) -> Any:
        """Handle 'batch_validate' method."""
        entities = params.get("entities")
        id_fields = params.get("id_fields")
        ruleset_name = params.get("ruleset_name")

        if entities is None:
            raise ValueError("Missing required parameter: entities")
        if id_fields is None:
            raise ValueError("Missing required parameter: id_fields")
        if ruleset_name is None:
            raise ValueError("Missing required parameter: ruleset_name")

        return self.service.batch_validate(entities, id_fields, ruleset_name)

    def _handle_batch_file_validate(self, params: Dict[str, Any]) -> Any:
        """Handle 'batch_file_validate' method."""
        file_uri = params.get("file_uri")
        entity_types = params.get("entity_types")
        id_fields = params.get("id_fields")
        ruleset_name = params.get("ruleset_name")

        if file_uri is None:
            raise ValueError("Missing required parameter: file_uri")
        if entity_types is None:
            raise ValueError("Missing required parameter: entity_types")
        if id_fields is None:
            raise ValueError("Missing required parameter: id_fields")
        if ruleset_name is None:
            raise ValueError("Missing required parameter: ruleset_name")

        return self.service.batch_file_validate(
            file_uri, entity_types, id_fields, ruleset_name
        )

    def _handle_reload_logic(self, params: Dict[str, Any]) -> Any:
        """Handle 'reload_logic' method."""
        # No parameters required
        self.service.reload_logic()
        return {"status": "ok", "message": "Logic reloaded successfully"}

    def _handle_get_cache_age(self, params: Dict[str, Any]) -> Any:
        """Handle 'get_cache_age' method."""
        # No parameters required
        age = self.service.get_cache_age()
        return {"cache_age": age}

    # Response formatting

    def _success_response(self, request_id: Any, result: Any) -> Dict[str, Any]:
        """Format successful JSON-RPC response."""
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error_response(
        self, request_id: Any, code: int, message: str, data: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Format JSON-RPC error response."""
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data

        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    def _send_response(self, response: Dict[str, Any], wfile=None) -> None:
        """
        Send a JSON-RPC response.

        Args:
            response: JSON-RPC response dict to serialise and send.
            wfile: Writable file-like object. Defaults to sys.stdout so
                   existing call sites (e.g. tests) need no changes.
        """
        if wfile is None:
            wfile = sys.stdout
        response_json = json.dumps(response)
        self._log(f"Sending: {response_json}")
        wfile.write(response_json + "\n")
        wfile.flush()


def main():
    """Main entry point for JSON-RPC server."""
    parser = argparse.ArgumentParser(
        description="ValidationService JSON-RPC 2.0 Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
  python -m validation_lib.jsonrpc_server
  python -m validation_lib.jsonrpc_server --debug

Supported methods:
  - validate
  - discover_rules
  - discover_rulesets
  - batch_validate
  - batch_file_validate
  - reload_logic
  - get_cache_age

Protocol: JSON-RPC 2.0 over stdin/stdout
See: https://www.jsonrpc.org/specification
        """,
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging to stderr"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="TCP port to listen on. If omitted, the server uses stdio (default).",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Bind address for TCP mode (default: 127.0.0.1 — loopback only). "
        "Ignored when --port is not set.",
    )

    args = parser.parse_args()

    # Create server
    server = ValidationJsonRpcServer(debug=args.debug)

    # Handle signals for graceful shutdown
    def signal_handler(sig, frame):
        server.stop_server()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Start server in selected transport mode
    if args.port is not None:
        server.start_tcp_server(args.host, args.port)
    else:
        server.start_stdio_server()


if __name__ == "__main__":
    main()
