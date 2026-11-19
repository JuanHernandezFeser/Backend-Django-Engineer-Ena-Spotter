from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from fuel.serializers import RouteRequestSerializer
from fuel.services.fuel import NoStationInRangeError
from fuel.services.geocoding import GeocodingError, LocationNotInUS
from fuel.services.planner import plan_route
from fuel.services.routing import RoutingError


def _error_status(exc: Exception) -> int:
    if isinstance(exc, (LocationNotInUS, ValueError)):
        return status.HTTP_400_BAD_REQUEST
    if isinstance(exc, NoStationInRangeError):
        return status.HTTP_422_UNPROCESSABLE_ENTITY
    return status.HTTP_502_BAD_GATEWAY


class RouteAPIView(APIView):
    """POST /api/route/ -> route GeoJSON, fuel stops and total cost."""

    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        start = serializer.validated_data["start"]
        finish = serializer.validated_data["finish"]

        try:
            result = plan_route(start, finish)
        except (LocationNotInUS, GeocodingError, RoutingError, NoStationInRangeError) as exc:
            return Response({"error": str(exc)}, status=_error_status(exc))

        return Response(result)


@require_GET
def route_map(request):
    start = request.GET.get("start", "").strip()
    finish = request.GET.get("finish", "").strip()
    if not start or not finish:
        return HttpResponse(
            "Provide ?start=...&finish=... (e.g. 'New York, NY').", status=400
        )

    try:
        result = plan_route(start, finish)
    except (LocationNotInUS, GeocodingError, RoutingError, NoStationInRangeError) as exc:
        return render(
            request,
            "fuel/route_map.html",
            {"error": str(exc), "start": start, "finish": finish,
             "route": None, "stops": []},
            status=_error_status(exc),
        )

    return render(request, "fuel/route_map.html", {"error": None, **result})
