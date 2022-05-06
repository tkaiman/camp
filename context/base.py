from django.conf import settings


def site_title(request):
    return {
        "site_title": settings.SITE_TITLE,
    }
