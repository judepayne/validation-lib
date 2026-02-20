"""
validation-lib-py: Business data validation library with dynamic rule loading

This library provides a pure Python validation service with:
- Dynamic rule loading from local or remote sources
- Two-tier configuration (infrastructure + business logic)
- JSON schema validation
- Custom Python business rules
- Entity helper abstraction layer
- Hot reload capabilities

Example:
    from validation_lib import ValidationService

    service = ValidationService()
    results = service.validate("loan", loan_data, "quick")
"""

from .api import ValidationService

__version__ = "0.1.0"
__all__ = ["ValidationService"]
