from django.urls import path

from .views import FuelRouteOptimizerView

app_name = "routing"

urlpatterns = [
    # POST /api/route/optimize/
    path(
        "route/optimize/",
        FuelRouteOptimizerView.as_view(),
        name="fuel-route-optimize",
    ),
]
