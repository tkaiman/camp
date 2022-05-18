from django.views.generic import TemplateView

from game.models import Game


class HomePageView(TemplateView):
    template_name = "pages/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["open_games"] = Game.objects.filter(is_open=True)
        return context
