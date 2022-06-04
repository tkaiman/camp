from django.conf import settings as _settings


def settings(request):
    return {
        "site_title": _settings.SITE_TITLE,
    }


def game(request):
    if request.site and hasattr(request.site, "game"):
        return {"game": request.site.game}
    return {"game": None}
