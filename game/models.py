from django.contrib.sites.models import Site
from django.db import models


class Game(models.Model):
    """Represents a top-level game.

    A game can have as many chapters and campaigns as desired,
    and each can run events with different rulesets if needed.
    Each game is associated with a particular subdomain.
    """

    site = models.OneToOneField(Site, on_delete=models.CASCADE)
    description = models.TextField()
    is_open = models.BooleanField(default=False)

    def __str__(self):
        return self.site.name

    def get_url(self, request):
        """Produces a fully-qualified URL.

        Needs a request to properly extract the scheme and port in use.
        """
        port = request.META["SERVER_PORT"]
        if (
            port == "80"
            and request.scheme == "http"
            or port == "443"
            and request.scheme == "https"
        ):
            port_str = ""
        else:
            port_str = ":" + port
        return f"{request.scheme}://{self.site.domain}{port_str}/"
