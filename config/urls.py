from django.urls import path

from fuel import views

urlpatterns = [
    path("api/route/", views.RouteAPIView.as_view(), name="api-route"),
    path("api/route/map/", views.route_map, name="api-route-map"),
]
