import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from infra.metrics_views import metrics_view


def health_view(request):
    return JsonResponse({
        'status': 'ok',
        'service': 'moderation',
    })


urlpatterns = [
    path('health/', health_view, name='health'),
    path('metrics/', metrics_view, name='metrics'),
    path('api/v1/', include('moderation_api.urls')),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='docs'),
]
