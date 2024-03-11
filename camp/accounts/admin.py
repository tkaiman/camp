from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from . import models


class MembershipInline(admin.StackedInline):
    model = models.Membership
    extra = 0


class UserAdmin(BaseUserAdmin):
    inlines = [MembershipInline]


# User is already registered, so we need to unregister it first.
admin.site.unregister(User)
admin.site.register(User, UserAdmin)
