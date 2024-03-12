from typing import Any

from django.contrib import admin
from django.db import transaction
from django.db.models import QuerySet

from camp.engine.rules.tempest.records import AwardCategory
from camp.game.models.game_models import Chapter

from . import models


class GameRoleInline(admin.TabularInline):
    model = models.GameRole


class ChapterRoleInline(admin.TabularInline):
    model = models.ChapterRole


class RulesetInline(admin.TabularInline):
    model = models.Ruleset


@admin.register(models.Game)
class GameAdmin(admin.ModelAdmin):
    inlines = [GameRoleInline, RulesetInline]


@admin.register(models.Chapter)
class ChapterAdmin(admin.ModelAdmin):
    inlines = [ChapterRoleInline]


@admin.register(models.Campaign)
class CampaignAdmin(admin.ModelAdmin):
    pass


class RegistrationInline(admin.TabularInline):
    model = models.EventRegistration


class ReportInline(admin.TabularInline):
    model = models.EventReport


@admin.register(models.Event)
class EventAdmin(admin.ModelAdmin):
    date_hierarchy = "created_date"
    inlines = [ReportInline, RegistrationInline]


@admin.register(models.EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    date_hierarchy = "registered_date"


@admin.register(models.EventReport)
class EventReportAdmin(admin.ModelAdmin):
    pass


@admin.register(models.PlayerCampaignData)
class PlayerCampaignAdmin(admin.ModelAdmin):
    pass


class AwardCategoryFilter(admin.SimpleListFilter):
    title = "Category"
    parameter_name = "category"

    def lookups(self, request, model_admin) -> list[tuple[Any, str]]:
        return [(ac, ac.label) for ac in AwardCategory]

    def queryset(self, request, queryset):
        if (v := self.value()) and v in AwardCategory:
            return queryset.filter(award_data__category=v)
        return queryset


class AwardAppliedFilter(admin.SimpleListFilter):
    title = "Applied"
    parameter_name = "applied"

    def lookups(self, request, model_admin):
        return [("yes", "Yes"), ("no", "No")]

    def queryset(self, request, queryset):
        match self.value():
            case "yes":
                return queryset.filter(applied_date__isnull=False)
            case "no":
                return queryset.filter(applied_date__isnull=True)
            case _:
                return queryset


class AwardChapterFilter(admin.SimpleListFilter):
    title = "Chapter"
    parameter_name = "chapter"

    def lookups(self, request, model_admin):
        return [("none", "None")] + [(c.slug, c.name) for c in Chapter.objects.all()]

    def queryset(self, request, queryset):
        if v := self.value():
            if v.lower() == "none":
                return queryset.filter(chapter=None)
            return queryset.filter(chapter__slug=self.value())
        return queryset


@admin.register(models.Award)
class AwardAdmin(admin.ModelAdmin):
    list_filter = [AwardCategoryFilter, AwardAppliedFilter, AwardChapterFilter]
    list_display = [
        "player",
        "character",
        "email",
        "applied_date",
        "chapter",
        "event",
        "category_label",
        "describe_secret",
    ]
    list_display_links = ["player", "describe_secret"]
    search_fields = [
        "player__username",
        "player__email",
        "player__first_name",
        "player__last_name",
        "character__name",
        "email",
        "award_data__description",
    ]
    actions = ["pop"]

    @admin.action(description="Pop this award off the attached character")
    @transaction.atomic
    def pop(self, request, queryset: QuerySet[models.Award]):
        for award in queryset:
            award.pop()
