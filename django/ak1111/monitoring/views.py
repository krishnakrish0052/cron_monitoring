from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from monitoring.health import get_full_health


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health_check(request):
    result = get_full_health()
    status_code = 200 if result["status"] == "ok" else 503
    return Response(result, status=status_code)
