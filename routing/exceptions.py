from rest_framework import status
from typing import Dict, Any

class RoutingBaseException(Exception):
    """Base for all routing exceptions."""
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail = "Routing service error"

class GeocodingError(RoutingBaseException):
    status_code = status.HTTP_400_BAD_REQUEST
    detail = "Failed to resolve location"

class RoutingAPIError(RoutingBaseException):
    status_code = status.HTTP_502_BAD_GATEWAY
    detail = "Routing provider unavailable"

class OptimizationError(RoutingBaseException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    detail = "No feasible fuel plan found"

class RouteUnfeasibleError(OptimizationError):
    detail = "Route exceeds vehicle range and no fuel stops available"


class RoutingAPIConfigError(RoutingBaseException):
    """Missing API key or config."""
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail = "Routing service misconfigured"

