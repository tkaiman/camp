from __future__ import annotations

from django import template

from camp.accounts.models import Membership
from camp.accounts.models import User

register = template.Library()


@register.simple_tag(takes_context=True)
def displayuser(context):
    game = context.get("game")
    user = context.get("user")
    if not game or not isinstance(user, User):
        return str(user)
    profile: Membership | None = Membership.objects.filter(user=user, game=game).first()
    if profile:
        return str(profile)
    return str(user)
