"""Helpers for validation result envelopes and object-level statuses."""

from typing import Any, Dict, Iterator, List


_STATUS_PRECEDENCE = {
    "PASS": 0,
    "NORUN": 1,
    "WARN": 2,
    "FAIL": 3,
    "ERROR": 4,
}


def iter_rule_results(results: List[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    """
    Yield rule results recursively, including nested children.

    Args:
        results: Hierarchical rule result list.

    Yields:
        Each rule result dict in depth-first order.
    """
    for result in results:
        yield result
        yield from iter_rule_results(result.get("children", []))


def derive_object_status(results: List[Dict[str, Any]]) -> str:
    """
    Derive object-level status from hierarchical rule results.

    Args:
        results: Hierarchical rule result list.

    Returns:
        Highest-precedence status. Empty results return "NORUN".
    """
    highest_status = "PASS"
    highest_rank = _STATUS_PRECEDENCE[highest_status]

    seen_any = False
    for result in iter_rule_results(results):
        seen_any = True
        status = result.get("status", "ERROR")
        rank = _STATUS_PRECEDENCE.get(status, _STATUS_PRECEDENCE["ERROR"])
        if rank > highest_rank:
            highest_status = status if status in _STATUS_PRECEDENCE else "ERROR"
            highest_rank = rank

    if not seen_any:
        return "NORUN"
    return highest_status


def build_validation_envelope(
    entity_type: str, ruleset_name: str, results: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Build object-level validation response envelope.

    Args:
        entity_type: Entity type validated.
        ruleset_name: Ruleset used.
        results: Hierarchical rule results.

    Returns:
        Object-level response envelope.
    """
    return {
        "status": derive_object_status(results),
        "entity_type": entity_type,
        "ruleset": ruleset_name,
        "results": results,
    }


def build_plugin_fail_envelope(
    entity_type: str,
    ruleset_name: str,
    plugin_name: str,
    plugin_message: str,
) -> Dict[str, Any]:
    """
    Build object-level plugin failure envelope.

    Args:
        entity_type: Entity type the plugin is declared to produce.
        ruleset_name: Requested ruleset.
        plugin_name: Plugin name from business config.
        plugin_message: User-facing plugin failure message.

    Returns:
        Object-level PLUGIN_FAIL response envelope.
    """
    return {
        "status": "PLUGIN_FAIL",
        "entity_type": entity_type,
        "ruleset": ruleset_name,
        "results": [],
        "plugin_name": plugin_name,
        "plugin_message": plugin_message,
    }
