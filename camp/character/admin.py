from django.contrib import admin

from camp.game import models as game_models

from . import models


class SheetAdminInline(admin.StackedInline):
    model = models.Sheet
    extra = 0
    can_delete = False


class AwardInline(admin.TabularInline):
    model = game_models.Award
    extra = 0
    can_delete = False
    show_change_link = True
    classes = ["collapse"]
    fields = ["applied_date", "chapter", "event", "describe_secret"]
    readonly_fields = ["applied_date", "chapter", "event", "describe_secret"]


@admin.register(models.Character)
class CharacterAdmin(admin.ModelAdmin):
    inlines = [SheetAdminInline, AwardInline]


class UndoStackEntryAdmin(admin.TabularInline):
    model = models.UndoStackEntry
    extra = 0


@admin.register(models.Sheet)
class SheetAdmin(admin.ModelAdmin):
    list_filter = ("character", "ruleset", "primary")
    inlines = [UndoStackEntryAdmin]
