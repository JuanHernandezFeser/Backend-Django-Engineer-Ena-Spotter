import logging

from django.shortcuts import render
from django.views.decorators.http import require_GET
from rest_framework.response import Response
from rest_framework.views import APIView

from fuel.exceptions import BadRequestError, DomainError, sanitize_retry_after
from fuel.serializers import RouteRequestSerializer
from fuel.services.planner import plan_route

logger = logging.getLogger(__name__)


class RouteAPIView(APIView):
    """POST /api/route/ -> route GeoJSON, fuel stops and total cost."""

    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        start = serializer.validated_data["start"]
        finish = serializer.validated_data["finish"]
        return Response(plan_route(start, finish))


@require_GET
def route_map(request):
    """Render an HTML map view for a route (same statuses/codes as the API)."""
    start = request.GET.get("start", "").strip()
    finish = request.GET.get("finish", "").strip()
    if not start or not finish:
        exc = BadRequestError(
            detail="Missing start or finish query parameter.",
            details={
                k: v
                for k, v in {
                    "start": "This field is required." if not start else None,
                    "finish": "This field is required." if not finish else None,
                }.items()
                if v
            },
        )
        logger.warning("domain_error code=%s detail=%s", exc.code, exc.detail)
        return render(
            request,
            "fuel/route_map.html",
            {
                "error": exc.message,
                "error_code": exc.code,
                "start": start,
                "finish": finish,
                "route": None,
                "stops": [],
            },
            status=exc.status_code,
        )

    try:
        result = plan_route(start, finish)
    except DomainError as exc:
        detail = exc.detail or str(exc)
        if exc.status_code >= 500:
            logger.error("domain_error code=%s detail=%s", exc.code, detail, exc_info=True)
        else:
            logger.warning("domain_error code=%s detail=%s", exc.code, detail)
        response = render(
            request,
            "fuel/route_map.html",
            {
                "error": exc.message,
                "error_code": exc.code,
                "start": start,
                "finish": finish,
                "route": None,
                "stops": [],
            },
            status=exc.status_code,
        )
        retry_after = sanitize_retry_after(exc.retry_after)
        if retry_after is not None:
            response["Retry-After"] = retry_after
        return response
    except Exception as exc:
        logger.exception("unexpected_error detail=%s", str(exc))
        return render(
            request,
            "fuel/route_map.html",
            {
                "error": "Internal server error.",
                "error_code": "internal_error",
                "start": start,
                "finish": finish,
                "route": None,
                "stops": [],
            },
            status=500,
        )

    return render(request, "fuel/route_map.html", {"error": None, "error_code": None, **result})
