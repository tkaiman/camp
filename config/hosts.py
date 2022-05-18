from django.conf import settings
from django_hosts import host
from django_hosts import patterns

host_patterns = patterns(
    "",
    host("www", "config.hub_urls", name="hub"),
    host(
        r"(?P<slug>[a-zA-Z0-9_-]+)",
        settings.ROOT_URLCONF,
        name="game",
        callback="context.callbacks.host_callback",
    ),
)
