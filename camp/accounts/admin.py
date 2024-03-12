from typing import Any

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.db.models.query import QuerySet
from django.http import HttpRequest

from camp.character import models as char_model

from . import models


@admin.register(models.Membership)
class MembershipAdmin(admin.ModelAdmin):
    pass


class MembershipInline(admin.StackedInline):
    model = models.Membership
    extra = 0
    show_change_link = True
    can_delete = False
    readonly_fields = ["game"]
    fields = ["game", "legal_name", "preferred_name", "birthdate"]


class CharacterInline(admin.TabularInline):
    model = char_model.Character
    fk_name = "owner"
    extra = 0
    show_change_link = True
    view_on_site = True
    can_delete = False
    fields = ["campaign", "is_discarded"]
    readonly_fields = ["campaign", "is_discarded"]
    classes = ["collapse"]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Any]:
        return super().get_queryset(request).filter(campaign__isnull=False)


class UserAdmin(BaseUserAdmin):
    inlines = [MembershipInline, CharacterInline]


# User is already registered, so we need to unregister it first.
admin.site.unregister(User)
admin.site.register(User, UserAdmin)
