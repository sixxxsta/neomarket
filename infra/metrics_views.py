from django.http import HttpResponse


def metrics_view(request):
    return HttpResponse("ok\n", content_type="text/plain; charset=utf-8")
