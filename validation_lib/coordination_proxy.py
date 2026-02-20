"""
Coordination Service Proxy

Provides interface to coordination service for fetching associated data
that validation rules need (parent entities, children, reference data, etc.).

The coordination service is a web application with JSON endpoints that provides:
- Hierarchical entity relationships (parent, all_children, all_siblings)
- Reference data (client_reference_data, legal_documents, etc.)
- Temporal data for historical checks

Current Status: Stubbed for POC - returns empty data
Future: HTTP client calling coordination service REST API
"""

import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class CoordinationProxy:
    """
    Proxy for coordination service calls.

    Wraps HTTP calls to coordination service for fetching associated data
    that validation rules need.
    """

    def __init__(self, coordination_service_config: Dict[str, Any]):
        """
        Initialize coordination proxy.

        Args:
            coordination_service_config: Coordination service configuration dict:
                    - enabled: Whether coordination service is enabled
                    - base_url: URL of coordination service
                    - timeout_ms: Request timeout in milliseconds
                    - retry_attempts: Number of retry attempts
                    - circuit_breaker_enabled: Enable circuit breaker (future)
        """
        self.config = coordination_service_config
        self.enabled = self.config.get('enabled', False)
        self.base_url = self.config.get('base_url')
        self.timeout_ms = self.config.get('timeout_ms', 5000)
        self.retry_attempts = self.config.get('retry_attempts', 3)

        if self.enabled:
            logger.info(
                "Coordination proxy initialized",
                extra={
                    'base_url': self.base_url,
                    'timeout_ms': self.timeout_ms,
                    'retry_attempts': self.retry_attempts
                }
            )
        else:
            logger.info("Coordination service disabled (POC mode)")

    def get_associated_data(
        self,
        entity_type: str,
        entity_data: Dict[str, Any],
        vocabulary_terms: List[str]
    ) -> Dict[str, Any]:
        """
        Fetch associated data from coordination service.

        Args:
            entity_type: Type of entity being validated ('loan', 'facility', 'deal')
            entity_data: The entity data being validated
            vocabulary_terms: List of vocabulary terms for required data
                            e.g., ['parent', 'all_children', 'client_reference_data']

        Returns:
            Dict mapping vocabulary terms to their data:
            {
                'parent': {...},
                'all_children': [{...}, {...}],
                'client_reference_data': {...}
            }

            Returns empty dict {} if coordination service disabled or unavailable.

        Future Implementation:
            Will POST to {base_url}/fetch-data with:
            {
                "entity_type": "loan",
                "entity_data": {...},
                "vocabulary_terms": ["parent", "all_children"]
            }

            And receive response:
            {
                "parent": {...},
                "all_children": [{...}]
            }
        """
        # POC Implementation: Return empty data
        if not self.enabled:
            logger.debug(
                "Coordination service disabled - returning empty data",
                extra={
                    'entity_type': entity_type,
                    'vocabulary_terms': vocabulary_terms
                }
            )
            return {}

        # Future: HTTP POST to coordination service
        logger.info(
            "STUB: Coordination service called (returning empty data)",
            extra={
                'entity_type': entity_type,
                'vocabulary_terms': vocabulary_terms,
                'endpoint': f"{self.base_url}/fetch-data"
            }
        )

        return {}

        # Future implementation would be:
        # try:
        #     import requests
        #
        #     payload = {
        #         'entity_type': entity_type,
        #         'entity_data': entity_data,
        #         'vocabulary_terms': vocabulary_terms
        #     }
        #
        #     response = requests.post(
        #         f"{self.base_url}/fetch-data",
        #         json=payload,
        #         timeout=self.timeout_ms / 1000.0
        #     )
        #     response.raise_for_status()
        #
        #     return response.json()
        #
        # except requests.exceptions.Timeout:
        #     logger.error("Coordination service timeout",
        #                 extra={'timeout_ms': self.timeout_ms})
        #     return {}
        # except requests.exceptions.RequestException as e:
        #     logger.error("Coordination service error",
        #                 extra={'error': str(e)})
        #     return {}
