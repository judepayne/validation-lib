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

import sys
import json
import signal
import argparse
from typing import Any, Dict, Optional, Tuple

from validation_lib import ValidationService


class ValidationJsonRpcServer:
    """JSON-RPC 2.0 server wrapping ValidationService API."""

    # JSON-RPC error codes
    ERROR_PARSE = -32700        # Invalid JSON
    ERROR_INVALID_REQUEST = -32600  # Invalid JSON-RPC structure
    ERROR_METHOD_NOT_FOUND = -32601  # Unknown method
    ERROR_INVALID_PARAMS = -32602   # Invalid parameters
    ERROR_INTERNAL = -32000      # Application error (catch-all)
    ERROR_VALIDATION = -32001    # Validation-specific error

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
            'validate': self._handle_validate,
            'discover_rules': self._handle_discover_rules,
            'discover_rulesets': self._handle_discover_rulesets,
            'batch_validate': self._handle_batch_validate,
            'batch_file_validate': self._handle_batch_file_validate,
            'reload_logic': self._handle_reload_logic,
            'get_cache_age': self._handle_get_cache_age,
        }

    def _log(self, message: str):
        """Log debug message to stderr (doesn't interfere with JSON-RPC on stdout)."""
        if self.debug:
            sys.stderr.write(f"[DEBUG] {message}\n")
            sys.stderr.flush()

    def start_server(self):
        """
        Start the JSON-RPC server loop.

        Reads requests from stdin, processes them, writes responses to stdout.
        Runs until EOF or stop signal received.
        """
        self.running = True
        self._log("ValidationService JSON-RPC server started")

        while self.running:
            try:
                # Read request from stdin
                line = sys.stdin.readline()

                if not line:
                    # EOF - clean shutdown
                    self._log("EOF received, shutting down")
                    break

                self._log(f"Received: {line.strip()}")

                # Process request
                response = self.handle_request(line)

                # Send response to stdout
                self._send_response(response)

            except KeyboardInterrupt:
                self._log("KeyboardInterrupt received, shutting down")
                break

            except Exception as e:
                # Fatal error in main loop
                self._log(f"Fatal error in main loop: {e}")
                import traceback
                traceback.print_exc(file=sys.stderr)
                break

        self._log("Server stopped")

    def stop_server(self):
        """
        Stop the server gracefully.

        Sets running flag to False, causing the main loop to exit.
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
                return self._error_response(None, self.ERROR_PARSE,
                                           f"Parse error: {e}")

            # Validate JSON-RPC structure
            if not isinstance(request, dict):
                return self._error_response(None, self.ERROR_INVALID_REQUEST,
                                           "Request must be a JSON object")

            if request.get("jsonrpc") != "2.0":
                return self._error_response(None, self.ERROR_INVALID_REQUEST,
                                           f"Invalid JSON-RPC version: {request.get('jsonrpc')}")

            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params", {})

            if not method:
                return self._error_response(request_id, self.ERROR_INVALID_REQUEST,
                                           "Missing 'method' field")

            if not isinstance(params, dict):
                return self._error_response(request_id, self.ERROR_INVALID_PARAMS,
                                           f"Params must be an object, got {type(params).__name__}")

            # Dispatch to method handler
            self._log(f"Dispatching method: {method}")
            result = self._dispatch(method, params)

            return self._success_response(request_id, result)

        except Exception as e:
            # Catch any unexpected errors
            self._log(f"Error processing request: {e}")
            return self._error_response(request_id, self.ERROR_INTERNAL,
                                       f"Internal error: {e}")

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
            raise ValueError(f"Method not found: {method}")

        handler = self.methods[method]
        return handler(params)

    # Method handlers - wrap ValidationService API

    def _handle_validate(self, params: Dict[str, Any]) -> Any:
        """Handle 'validate' method."""
        entity_type = params.get('entity_type')
        entity_data = params.get('entity_data')
        ruleset_name = params.get('ruleset_name')

        if not entity_type:
            raise ValueError("Missing required parameter: entity_type")
        if not entity_data:
            raise ValueError("Missing required parameter: entity_data")
        if not ruleset_name:
            raise ValueError("Missing required parameter: ruleset_name")

        return self.service.validate(entity_type, entity_data, ruleset_name)

    def _handle_discover_rules(self, params: Dict[str, Any]) -> Any:
        """Handle 'discover_rules' method."""
        entity_type = params.get('entity_type')
        entity_data = params.get('entity_data')
        ruleset_name = params.get('ruleset_name')

        if not entity_type:
            raise ValueError("Missing required parameter: entity_type")
        if not entity_data:
            raise ValueError("Missing required parameter: entity_data")
        if not ruleset_name:
            raise ValueError("Missing required parameter: ruleset_name")

        return self.service.discover_rules(entity_type, entity_data, ruleset_name)

    def _handle_discover_rulesets(self, params: Dict[str, Any]) -> Any:
        """Handle 'discover_rulesets' method."""
        # No parameters required
        return self.service.discover_rulesets()

    def _handle_batch_validate(self, params: Dict[str, Any]) -> Any:
        """Handle 'batch_validate' method."""
        entities = params.get('entities')
        id_fields = params.get('id_fields')
        ruleset_name = params.get('ruleset_name')

        if not entities:
            raise ValueError("Missing required parameter: entities")
        if not id_fields:
            raise ValueError("Missing required parameter: id_fields")
        if not ruleset_name:
            raise ValueError("Missing required parameter: ruleset_name")

        return self.service.batch_validate(entities, id_fields, ruleset_name)

    def _handle_batch_file_validate(self, params: Dict[str, Any]) -> Any:
        """Handle 'batch_file_validate' method."""
        file_uri = params.get('file_uri')
        entity_types = params.get('entity_types')
        id_fields = params.get('id_fields')
        ruleset_name = params.get('ruleset_name')

        if not file_uri:
            raise ValueError("Missing required parameter: file_uri")
        if not entity_types:
            raise ValueError("Missing required parameter: entity_types")
        if not id_fields:
            raise ValueError("Missing required parameter: id_fields")
        if not ruleset_name:
            raise ValueError("Missing required parameter: ruleset_name")

        return self.service.batch_file_validate(file_uri, entity_types, id_fields, ruleset_name)

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
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result
        }

    def _error_response(self, request_id: Any, code: int, message: str,
                       data: Optional[Any] = None) -> Dict[str, Any]:
        """Format JSON-RPC error response."""
        error = {
            "code": code,
            "message": message
        }
        if data is not None:
            error["data"] = data

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": error
        }

    def _send_response(self, response: Dict[str, Any]):
        """Send JSON-RPC response to stdout."""
        response_json = json.dumps(response)
        self._log(f"Sending: {response_json}")
        sys.stdout.write(response_json + "\n")
        sys.stdout.flush()


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
        """
    )
    parser.add_argument('--debug', action='store_true',
                       help='Enable debug logging to stderr')

    args = parser.parse_args()

    # Create and start server
    server = ValidationJsonRpcServer(debug=args.debug)

    # Handle signals for graceful shutdown
    def signal_handler(sig, frame):
        server.stop_server()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Start server (blocks until stopped)
    server.start_server()


if __name__ == "__main__":
    main()
