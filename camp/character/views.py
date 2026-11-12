from __future__ import annotations

import dataclasses
import datetime
import logging
from collections import defaultdict
from functools import cached_property
from itertools import chain
from typing import Iterable
from typing import cast
from typing import override

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.db import transaction
from django.http import Http404
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST
from django.views.generic import CreateView
from django.views.generic import DetailView
from django.views.generic import ListView
from django_htmx.http import HttpResponseClientRedirect
from django_htmx.http import reswap
from django_htmx.http import trigger_client_event
from rules.contrib.views import AutoPermissionRequiredMixin
from rules.contrib.views import objectgetter
from rules.contrib.views import permission_required
from sentry_sdk import capture_exception

from camp.character.models import Character
from camp.character.models import Sheet
from camp.engine.rules.base_engine import BaseFeatureController
from camp.engine.rules.base_engine import CharacterController
from camp.engine.rules.base_models import ChoiceMutation
from camp.engine.rules.base_models import Mutation
from camp.engine.rules.base_models import MutationAdapter
from camp.engine.rules.base_models import PropExpression
from camp.engine.rules.base_models import RankMutation
from camp.engine.rules.base_models import dump_mutation
from camp.engine.rules.decision import Decision
from camp.engine.rules.tempest.controllers.character_controller import TempestCharacter
from camp.engine.rules.tempest.records import AwardCategory
from camp.engine.rules.tempest.records import AwardRecord
from camp.game import models as game_models

from . import forms
from .templatetags.character_sheet import feature_url

FEATURE_MODAL_TEMPLATE = "character/partials/_feature_modal.html"
AVAILABLE_GROUP_MODAL_TEMPLATE = "character/partials/_available_group_modal.html"
MANAGE_MODAL_TEMPLATE = "character/partials/_manage_modal.html"
# Sheet-level forms that can be loaded into the character modal.
MANAGE_ACTIONS = frozenset({"attrs", "name", "delete", "copy"})


class CharacterListView(LoginRequiredMixin, ListView):
    model = Character

    def get_queryset(self):
        if self.request.user.is_authenticated:
            return self.model.objects.filter(owner=self.request.user)
        return self.model.objects.none()


class CharacterView(AutoPermissionRequiredMixin, DetailView):
    model = Character

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context.update(_character_context(self.request, self.object))
        return context


class CreateCharacterView(AutoPermissionRequiredMixin, CreateView):
    model = Character
    form_class = forms.NewCharacterForm

    @property
    def success_url(self):
        if self.object:
            return reverse("character-detail", args=[self.object.id])
        return "/"

    @override
    def get_initial(self) -> dict:
        kwargs = super().get_initial()
        if campaign := forms.OPEN_CAMPAIGNS.first():
            kwargs["campaign"] = campaign
        return kwargs

    def form_valid(self, form):
        """If the form is valid, save the associated model."""
        self.object = form.save(commit=False)
        self.object.owner = self.request.user
        self.object.game = self.request.game
        try:
            self.object.save()
        except IntegrityError:
            return super().form_invalid(form)
        # This has a side effect of creating a primary sheet if one doesn't exist.
        _ = self.object.primary_sheet
        return super().form_valid(form)


def _is_htmx(request) -> bool:
    """Whether the request expects a partial (HTMX) response.

    Boosted requests are ordinary navigation with a faster page swap, so they
    get the same full pages and redirects as requests made without HTMX.
    """
    htmx = getattr(request, "htmx", None)
    return bool(htmx and not htmx.boosted)


def _character_feature_groups(controller: CharacterController) -> list[FeatureGroup]:
    taken_features = controller.list_features(taken=True, available=False)
    available_features = controller.list_features(available=True, taken=False)
    return _features(controller, chain(taken_features, available_features))


def _character_feature_group(
    controller: CharacterController, group_key: str
) -> FeatureGroup | None:
    """Find one of the character's feature groups by key.

    Working out what's available is expensive, so this only considers
    the features that could be in the group, rather than building all of
    the groups and picking one.
    """
    for feature_type in {d.type for d in controller.ruleset.features.values()}:
        # The key of a group starts with the type of the features in it.
        type_key = slugify(feature_type)
        if group_key != type_key and not group_key.startswith(f"{type_key}-"):
            continue
        features = chain(
            controller.list_features(type=feature_type, taken=True, available=False),
            controller.list_features(type=feature_type, available=True, taken=False),
        )
        for group in _features(controller, features):
            if group.key == group_key:
                return group
    return None


def _character_context(request, character: Character) -> dict:
    """Context for the character header and summary, read fresh from the database."""
    # Some day, when we support multiple sheets, this will need to be smarter.
    sheet: Sheet = cast(Sheet, character.primary_sheet)
    controller: CharacterController = sheet.controller
    context = {
        "character": character,
        "sheet": sheet,
        "controller": controller,
        "feature_groups": _character_feature_groups(controller),
        "undo": sheet.last_undo,
        "issues": controller.issues(),
        # This is the sheet itself, rather than a page about some part of it.
        "is_sheet": True,
    }
    if not (rd := controller.validate()):
        messages.error(request, "Error validating character: %s" % rd.reason)
    return context


def _htmx_character_response(
    request,
    character: Character,
    *,
    modal_html: str = "",
    close_modal: bool = False,
) -> HttpResponse:
    """Respond to an HTMX request that may have changed the character.

    The response carries the new modal content (if any) along with out-of-band
    replacements for the character header, summary, and messages. Sending it all
    at once means the sheet can't get out of sync with the modal, and messages
    are consumed by the same request that produced them.
    """
    character.refresh_from_db()
    context = _character_context(request, character)
    oob_html = render_to_string(
        "character/partials/_oob_updates.html", context, request=request
    )
    response = HttpResponse(modal_html + oob_html)
    if not modal_html:
        # Leave whatever is in the modal alone, rather than emptying it.
        reswap(response, "none")
    if close_modal:
        trigger_client_event(response, "close-character-modal")
    return response


def _htmx_modal_response(request, modal_html: str) -> HttpResponse:
    """Replace the modal content and messages, leaving the sheet untouched."""
    messages_html = render_to_string(
        "snippets/messages.html", {"oob": True}, request=request
    )
    return HttpResponse(modal_html + messages_html)


def _sheet_response(request, character: Character) -> HttpResponse:
    """Finish an action by returning to the (updated) character sheet."""
    if _is_htmx(request):
        return _htmx_character_response(request, character, close_modal=True)
    return redirect(character)


def _render_modal(request, template_name: str, context: dict) -> HttpResponse:
    """Render modal content, as a partial for HTMX or wrapped in a page otherwise."""
    if _is_htmx(request):
        return render(request, template_name, {**context, "in_modal": True})
    return render(
        request,
        "character/modal_page.html",
        {**context, "modal_template": template_name},
    )


@permission_required(
    "character.change_character", fn=objectgetter(Character), raise_exception=True
)
@require_POST
def delete_character(request, pk):
    character = get_object_or_404(Character, pk=pk)
    if character.campaign is None:
        # Freeplay characters can be freely deleted.
        character.delete()
        messages.warning(request, "Freeplay character permanently deleted.")
    else:
        if character.discarded_date is None:
            character.discarded_date = timezone.now()
            character.discarded_by = request.user
            character.save()
            messages.warning(request, "Character discarded.")
        else:
            messages.warning(request, "Character was already discarded.")
    if _is_htmx(request):
        return HttpResponseClientRedirect(reverse("home"))
    return redirect("home")


@permission_required(
    "character.change_character", fn=objectgetter(Character), raise_exception=True
)
@require_POST
def set_attr(request, pk):
    """Set the character's Level or CP to an arbitrary value."""
    # TODO: Restrict this to either freeplay mode or maybe logistics action.
    character = get_object_or_404(Character, id=pk)
    if character.is_discarded:
        messages.warning(request, "Can't update discarded character.")
        return _sheet_response(request, character)
    if character.campaign is not None:
        raise ValueError(
            f"User {request.user} can't edit attributes for campaign character {character.id}"
        )
    sheet = character.primary_sheet
    controller = sheet.controller
    if "level" in request.POST:
        level = request.POST["level"]
        try:
            level = int(level)
        except ValueError:
            messages.error(request, "Level must be an integer.")
            return _sheet_response(request, character)
        if controller.xp_level != level:
            controller.xp_level = level
            messages.success(request, f"Level set to {level}.")
    if "cp" in request.POST:
        cp = request.POST["cp"]
        try:
            cp = int(cp)
        except ValueError:
            messages.error(request, "Awarded CP must be an integer.")
            return _sheet_response(request, character)
        if controller.freeplay_cp != cp:
            controller.freeplay_cp = cp
            messages.success(request, f"Awarded CP set to {cp}.")

    if d := controller.validate():
        sheet.save()
    else:
        messages.error(request, "Error validating character: %s" % d.reason)
    return _sheet_response(request, character)


@permission_required(
    "character.change_character", fn=objectgetter(Character), raise_exception=True
)
@require_POST
def apply_view(request, pk):
    character: Character = get_object_or_404(Character, pk=pk)
    if character.is_discarded:
        messages.warning(request, "Can't update discarded character.")
        return _sheet_response(request, character)
    sheet = character.primary_sheet
    controller = cast(TempestCharacter, sheet.controller)
    mutation = MutationAdapter.validate_json(request.POST.get("mutation", "{}"))

    # TODO: Certain mutations may need additional privileges.

    if result := _apply_mutation(mutation, sheet, controller, request):
        messages.success(request, result.reason or "Change applied.")
    else:
        messages.error(request, result.reason or "Could not apply change.")
    return _sheet_response(request, character)


@permission_required(
    "character.change_character", fn=objectgetter(Character), raise_exception=True
)
@require_POST
def set_name(request, pk):
    """Sets the character's name."""
    character = get_object_or_404(Character, id=pk)
    if character.is_discarded:
        messages.warning(request, "Can't update discarded character.")
    elif new_name := request.POST.get("name", "").strip():
        character.name = new_name
        character.save()
        messages.success(request, f"Name changed to {new_name}")
    else:
        messages.error(request, "Character name can't be empty.")
    return _sheet_response(request, character)


@permission_required(
    "character.view_character", fn=objectgetter(Character), raise_exception=True
)
@require_GET
def manage_view(request, pk, action):
    """Serve one of the sheet-level forms (rename, copy, etc.) for the character modal."""
    character = get_object_or_404(Character, pk=pk)
    # Copying only reads the character. Everything else changes it.
    if action != "copy" and not request.user.has_perm(
        "character.change_character", character
    ):
        raise PermissionDenied
    if action not in MANAGE_ACTIONS:
        raise Http404(f"Unknown action '{action}'.")
    if action == "attrs" and character.campaign is not None:
        raise Http404("Attributes can only be set on freeplay characters.")
    context = {
        "character": character,
        "controller": character.primary_sheet.controller,
        "action": action,
    }
    return _render_modal(request, MANAGE_MODAL_TEMPLATE, context)


@require_GET
def available_group_view(request, pk, group_type):
    """List the features of one group that the character could add.

    This is loaded on demand when adding to a group, rather than rendering
    every available feature along with the sheet.
    """
    character = get_object_or_404(Character, pk=pk)
    # Respond as though the character doesn't exist, rather than confirming
    # that it does to someone who isn't allowed to see it.
    if not request.user.has_perm("character.view_character", character):
        raise Http404("No Character matches the given query.")
    controller = character.primary_sheet.controller
    group = _character_feature_group(controller, group_type)
    if group is None or not group.has_available:
        raise Http404(f"Nothing of type '{group_type}' is available.")
    context = {
        "character": character,
        "controller": controller,
        "group": group,
    }
    return _render_modal(request, AVAILABLE_GROUP_MODAL_TEMPLATE, context)


@permission_required(
    "character.change_character", fn=objectgetter(Character), raise_exception=True
)
def feature_view(request, pk, feature_id):
    character = get_object_or_404(Character, id=pk)
    if character.is_discarded:
        messages.warning(request, "Can't update discarded character.")
        return _sheet_response(request, character)
    sheet = character.primary_sheet
    controller = cast(TempestCharacter, sheet.controller)
    feature_controller = _find_feature(controller, feature_id)
    if feature_controller is None:
        raise Http404(f"Feature with ID '{feature_id}' not found or is seeekrit.")
    group_back = _group_back(request, controller, feature_controller)

    purchase_form = None
    if request.method == "POST":
        # Removing a feature can leave nothing behind to ask about its parent.
        parent = feature_controller.parent if feature_controller.internal else None
        parent_id = parent.full_id if parent else None
        is_choice = False
        applied = False
        if "purchase" in request.POST or "remove" in request.POST:
            feature_controller, can_inc, can_dec = _purchase_state(
                controller, feature_controller
            )
            if can_inc or can_dec:
                applied, purchase_form = _try_apply_purchase(
                    sheet, feature_id, feature_controller, controller, request
                )
        elif "choice" in request.POST:
            is_choice = True
            applied = _try_apply_choice(sheet, feature_id, controller, request)
            if not _is_htmx(request):
                return redirect("character-feature-view", pk=pk, feature_id=feature_id)
        if applied:
            return _mutation_response(
                request,
                character,
                controller,
                feature_id,
                parent_id=parent_id,
                is_choice=is_choice,
            )
        # A rejected mutation invalidates the controllers we were holding.
        feature_controller = _find_feature(controller, feature_id)
        if feature_controller is None:
            raise Http404(f"Feature with ID '{feature_id}' not found or is seeekrit.")

    context = _build_feature_context(
        character,
        controller,
        feature_controller,
        purchase_form=purchase_form,
        group_back=group_back,
    )
    if not _is_htmx(request):
        return render(request, "character/feature_form.html", context)
    modal_html = render_to_string(FEATURE_MODAL_TEMPLATE, context, request=request)
    if request.method == "POST":
        # The mutation was rejected, so the sheet behind the modal hasn't changed.
        return _htmx_modal_response(request, modal_html)
    return HttpResponse(modal_html)


def _find_feature(
    controller: CharacterController, feature_id: str
) -> BaseFeatureController | None:
    try:
        return controller.feature_controller(feature_id)
    except (ValueError, LookupError):
        return None


def _group_back(
    request, controller: CharacterController, feature_controller: BaseFeatureController
) -> dict[str, str] | None:
    """The group to offer a link back to, if the feature was reached from its list."""
    key = request.GET.get("group")
    if not key or key != _group_key(feature_controller):
        return None
    name = controller.plural_name(_group_type(feature_controller))
    return {"key": key, "name": name}


def _purchase_state(
    controller: CharacterController, feature_controller: BaseFeatureController
) -> tuple[BaseFeatureController, Decision, bool]:
    """Work out whether ranks of the feature can be added or removed.

    Returns the feature controller to use from here on, along with the
    decisions on whether it can be increased and decreased.
    """
    feature_id = feature_controller.full_id
    can_dec = bool(feature_controller.can_decrease())
    if can_dec:
        # Ok, but can we _really_ decrease?
        dec_mutation = RankMutation(id=feature_id, ranks=-1)
        if not controller.apply(dec_mutation, raise_exc=False, dry_run=True):
            can_dec = False
        # The old controller is tainted, reload it.
        feature_controller = controller.feature_controller(feature_id)
    can_inc = feature_controller.can_increase()
    return feature_controller, can_inc, can_dec


def _build_feature_context(
    character: Character,
    controller: CharacterController,
    feature_controller: BaseFeatureController,
    *,
    purchase_form: forms.FeatureForm | None = None,
    group_back: dict[str, str] | None = None,
) -> dict:
    """Context for the feature detail, whether shown as a page or in the modal.

    Pass `purchase_form` to show a submitted form along with its errors.
    Otherwise, a fresh form is created if the feature can be changed at all.
    """
    feature_controller, can_inc, can_dec = _purchase_state(
        controller, feature_controller
    )

    if purchase_form is not None:
        if purchase_form.is_bound:
            # Rebuild against the current controller, keeping the submission.
            purchase_form = forms.FeatureForm(feature_controller, purchase_form.data)
    elif can_inc or can_dec:
        data = None
        if feature_controller.is_concrete:
            # Only use initial values if this is an existing concrete feature instance.
            current = feature_controller.value
            data = {"ranks": current if can_dec else feature_controller.next_value}
            if feature_controller.option:
                data["option"] = feature_controller.option
        purchase_form = forms.FeatureForm(feature_controller, initial=data)

    issues = list(feature_controller.issues() or [])
    if feature_controller.value > 0 and feature_controller.supports_child_purchases:
        subfeatures = feature_controller.subfeatures
        subfeatures_available = _features(
            controller,
            feature_controller.subfeatures_available,
            hide_internal=False,
        )
    else:
        subfeatures = []
        subfeatures_available = []
    for sf in subfeatures:
        if sf_issues := sf.issues():
            issues.extend(sf_issues)

    choices = feature_controller.choices or {}
    context = {
        "character": character,
        "controller": controller,
        "feature": feature_controller,
        # Forms can be shown on other pages (in the modal), so they need to
        # know where to submit to. The group is kept so the link back survives.
        "feature_url": feature_url(
            character, feature_controller, group=(group_back or {}).get("key")
        ),
        "group_back": group_back,
        "subfeatures": subfeatures,
        "subfeatures_available": subfeatures_available,
        "explain_ranks": feature_controller.explain,
        "choices": {
            # More than one choice can be on the page, so IDs include the choice.
            k: forms.ChoiceForm(c, auto_id=f"id_{k}_%s")
            for (k, c) in choices.items()
        },
        "purchase_form": purchase_form,
        "issues": issues,
    }
    if not can_inc:
        context["no_purchase_reason"] = can_inc.reason
    return context


def _mutation_response(
    request,
    character: Character,
    controller: CharacterController,
    feature_id: str,
    *,
    parent_id: str | None,
    is_choice: bool,
) -> HttpResponse:
    """Decide where to go after a feature has been successfully changed.

    If there is more to do on the feature (choices to make, or things to
    purchase within it), stay on it. If it lives inside another feature, go
    back to that one. Otherwise, we're done, so return to the character sheet.
    """
    target: BaseFeatureController | None = None
    feature_controller = _find_feature(controller, feature_id)
    if feature_controller is None:
        # The feature is gone entirely, but we still know where it used to live.
        parent = _find_feature(controller, parent_id) if parent_id else None
        if parent and parent.value > 0:
            target = parent
    else:
        taken = feature_controller.value > 0
        has_choices = feature_controller.has_available_choices
        has_purchases = taken and bool(feature_controller.subfeatures_available)
        if (taken and has_choices) or has_purchases or (is_choice and has_choices):
            if not is_choice:
                if has_choices:
                    messages.info(request, "Choices are available for this feature.")
                if has_purchases:
                    messages.info(
                        request, "Purchases are available within this feature."
                    )
            target = feature_controller
        elif (
            feature_controller.internal
            and (parent := feature_controller.parent)
            and parent.value > 0
        ):
            target = parent

    if not _is_htmx(request):
        if target is None:
            return redirect(character)
        return redirect(
            "character-feature-view", pk=character.id, feature_id=target.full_id
        )
    if target is None:
        return _htmx_character_response(request, character, close_modal=True)
    # There's no link back to the group the feature was found in from here.
    # Having just changed the character, that list may not even exist anymore.
    context = _build_feature_context(character, controller, target)
    modal_html = render_to_string(FEATURE_MODAL_TEMPLATE, context, request=request)
    return _htmx_character_response(request, character, modal_html=modal_html)


def _try_apply_choice(
    sheet: Sheet, feature_id: str, controller: TempestCharacter, request
) -> bool:
    if "selection" not in request.POST:
        messages.error(request, "No selection made.")
        return False
    mutation = ChoiceMutation(
        id=feature_id,
        choice=request.POST["choice"],
        value=request.POST["selection"],
        remove=request.POST.get("unchoose", False),
    )
    if result := _apply_mutation(mutation, sheet, controller, request):
        messages.success(request, result.reason)
        return True
    messages.error(request, result.reason or "Could not apply choice.")
    return False


def _try_apply_purchase(
    sheet: Sheet,
    feature_id: str,
    feature_controller: BaseFeatureController,
    controller: TempestCharacter,
    request,
) -> tuple[bool, forms.FeatureForm]:
    pf = forms.FeatureForm(feature_controller, request.POST)
    if pf.is_valid():
        if request.POST.get("remove"):
            ranks = 0
        else:
            ranks = int(pf.cleaned_data.get("ranks", 1))
        if feature_controller.is_concrete:
            ranks -= feature_controller.value
        if ranks == 0:
            messages.info(request, "No change requested.")
            return True, pf
        expr = PropExpression.parse(feature_id)
        rm = RankMutation(
            id=expr.prop,
            ranks=ranks,
            option=pf.selected_option() or expr.option,
        )
        # Removing the last rank of some features leaves no controller behind.
        name = feature_controller.display_name()
        try:
            if result := _apply_mutation(rm, sheet, controller, request):
                if updated := _find_feature(controller, expr.full_id):
                    name = updated.display_name()
                if ranks > 0:
                    messages.success(request, f"{name} x{ranks} purchased.")
                elif ranks < 0:
                    messages.warning(request, f"{name} x{abs(ranks)} refunded.")
                else:
                    messages.info(request, f"{name} applied.")
                return True, pf
            elif result.needs_option:
                messages.error(request, "This feature requires an option.")
            elif result.reason:
                messages.error(request, result.reason)
            else:
                messages.error(
                    request, "Could not apply mutation for unspecified reasons."
                )
        except Exception as exc:
            logging.exception("Error applying mutation")
            capture_exception(exc)
            messages.error(request, f"Error applying mutation: {exc}")
    return False, pf


@permission_required(
    "character.change_character", fn=objectgetter(Character), raise_exception=True
)
def undo_view(request, pk):
    character = get_object_or_404(Character, id=pk)
    if character.is_discarded:
        messages.warning(request, "Can't update discarded character.")
        return _sheet_response(request, character)
    sheet = character.primary_sheet
    if request.POST and "undo" in request.POST:
        with transaction.atomic():
            last_undo = sheet.last_undo
            if not last_undo:
                messages.error(request, "Nothing to undo.")
                return _sheet_response(request, character)
            if request.POST.get("undo") == str(last_undo.id):
                mutation = sheet.undo()
                if mutation:
                    description = f"'{sheet.controller.describe_mutation(mutation)}'"
                else:
                    description = "the last action"
                messages.success(request, f"Undid {description}")
                return _sheet_response(request, character)
    messages.error(request, "Invalid undo request.")
    return _sheet_response(request, character)


@permission_required(
    "character.view_character", fn=objectgetter(Character), raise_exception=True
)
@require_POST
def copy_view(request, pk):
    with transaction.atomic():
        character = get_object_or_404(Character, id=pk)
        sheet = character.primary_sheet
        model = sheet.controller.model
        name = request.POST.get("name", f"Copy of {character.name}")
        new_character = Character.objects.create(
            owner=request.user,
            campaign=None,
            game=request.game,
            name=name,
        )
        new_sheet = new_character.primary_sheet
        controller = new_sheet.controller
        new_model = model.model_copy(
            update={
                "name": name,
            },
        )
        controller.model = new_model
        new_sheet.controller = controller
        new_sheet.save()
    if _is_htmx(request):
        return HttpResponseClientRedirect(new_character.get_absolute_url())
    return redirect(new_character)


def _features(
    controller: CharacterController,
    feats: Iterable[BaseFeatureController],
    hide_internal: bool = True,
    use_type_name: bool = False,
) -> list[FeatureGroup]:
    by_type: dict[str, FeatureGroup] = {}
    taken_ids = set()
    for feat in feats:
        if hide_internal and feat.internal and feat.parent and feat.parent.value > 0:
            # Don't include internal features in the list, since they will appear
            # nested under their parents. But, if the parent would not be shown
            # because it doesn't exist or doesn't have ranks, show it anyway.
            continue

        feature_type = _group_type(feat, use_type_name)

        if feature_type not in by_type:
            by_type[feature_type] = FeatureGroup(
                type=feat.feature_type,
                key=_group_key(feat, use_type_name),
                name=controller.plural_name(feature_type),
                priority=controller.display_priority(feature_type),
            )
        group = by_type[feature_type]
        if feat.should_show_in_list:
            # if feat not in group.taken:
            if feat.full_id not in taken_ids:
                taken_ids.add(feat.full_id)
                group.taken.append(feat)
        elif feat.is_option_template:
            # Option templates should only appear in the available list,
            # and only if another option is available.
            if feat.can_take_new_option:
                group.add_available(feat)
            # Otherwise, we don't care about them.
        else:
            group.add_available(feat)
    groups: list[FeatureGroup] = list(t for t in by_type.values() if t)
    for group in groups:
        group.sort()
    groups.sort(key=FeatureGroup.sortkey)
    return groups


def _group_type(feat: BaseFeatureController, use_type_name: bool = False) -> str:
    """The type that a feature is grouped under when listed.

    Subfeatures all share one feature type, so they are grouped by type name
    instead (ex: a class might have both "Paths" and "Specializations").
    """
    if use_type_name or feat.feature_type == "subfeature":
        return feat.type_name
    return feat.feature_type


def _group_key(feat: BaseFeatureController, use_type_name: bool = False) -> str:
    """Identifies the group a feature is listed in, for use in URLs and DOM IDs.

    Groups are usually one per feature type, so that's the key. Where a type
    is split into several groups by type name, the type name is included too.
    """
    key = slugify(feat.feature_type)
    if _group_type(feat, use_type_name) != feat.feature_type:
        key = f"{key}-{slugify(feat.type_name)}"
    return key


@dataclasses.dataclass
class FeatureGroup:
    """Features of one type, split into those taken and those available.

    Attributes:
        type: The raw feature type of the group's features. Subfeature
            groups all have the type "subfeature".
        name: Display name of the group.
        key: Slug that distinguishes this group from the others it is listed
            with, even when they share a `type`. Safe for URLs and DOM IDs.
            See `_group_key`.
    """

    type: str
    name: str
    key: str = ""
    taken: list[BaseFeatureController] = dataclasses.field(default_factory=list)
    available: list[BaseFeatureController] = dataclasses.field(default_factory=list)
    available_categories: dict[str, list[BaseFeatureController]] = dataclasses.field(
        default_factory=lambda: defaultdict(list)
    )
    priority: int = 1
    category_priority: dict[str, int] = dataclasses.field(default_factory=dict)

    @property
    def need_collapse(self) -> bool:
        return len(self.available_categories) > 1

    @property
    def has_available(self) -> bool:
        return self.available or self.available_categories

    def sortkey(self) -> tuple[int, str]:
        return self.priority, self.name

    def __bool__(self) -> bool:
        return bool(self.taken or self.available or self.available_categories)

    def explain(self) -> str | None:
        for feat in self.all():
            return feat.explain_type_group
        return None

    @cached_property
    def explain_list(self) -> list[str]:
        for feat in self.all():
            return feat.explain_list
        return []

    def add_available(self, feat: BaseFeatureController):
        if feat.category:
            self.available_categories[feat.category].append(feat)
            self.category_priority[feat.category] = feat.category_priority
        else:
            self.available.append(feat)

    def all(self) -> Iterable[BaseFeatureController]:
        yield from self.taken
        yield from self.available
        for cat in self.available_categories.values():
            yield from cat

    def sort(self):
        # Sort the base taken/available lists by name.
        self.taken.sort(key=lambda f: f.sort_key())
        self.available.sort(key=lambda f: f.sort_key())
        # Sort the categories themselves
        cats = self.available_categories
        self.available_categories = {k: cats[k] for k in sorted(cats)}
        # Sort the items in each category
        for cat in self.available_categories.values():
            cat.sort(key=lambda f: f.sort_key())
        # Sort the categories themselves. This is mostly by name, but a few categories
        # (those that contain tiered abilities) have priority equal to their tier.
        cats = sorted(
            self.available_categories.keys(),
            key=lambda k: (self.category_priority.get(k, 0), k),
        )
        self.available_categories = {k: self.available_categories[k] for k in cats}


def _apply_mutation(
    mutation: Mutation, sheet: Sheet, controller: TempestCharacter, request
) -> Decision:
    undo_data = controller.dump_dict()
    result = controller.apply(mutation)
    if result:
        with transaction.atomic():
            sheet.save()
            sheet.undo_stack.create(
                mutation=dump_mutation(mutation),
                previous_data=undo_data,
            )
            if len(sheet.undo_stack.all()) > settings.UNDO_STACK_SIZE:
                sheet.undo_stack.order_by("timestamp").first().delete()
            if result.metadata_update and sheet.character.campaign:
                record: AwardRecord = AwardRecord(
                    date=datetime.date.today(),
                    source_id=None,
                    category=AwardCategory.CHARACTER,
                    description=result.metadata_update.description,
                    character=sheet.character.id,
                    character_flags=result.metadata_update.character_flags,
                )
                award: game_models.Award = game_models.Award.objects.create(
                    campaign=sheet.character.campaign,
                    player=sheet.character.owner,
                    character=sheet.character,
                    awarded_by=request.user,
                    award_data=record.model_dump(mode="json"),
                )
                award.apply()
                messages.warning(request, "Plot will remember that.")
    return result
