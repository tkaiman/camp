from __future__ import annotations

import html
import itertools
import re
from typing import Type
from typing import TypeVar
from urllib.parse import unquote
from urllib.parse import urlencode

from django import template
from django.urls import NoReverseMatch
from django.urls import reverse
from django.utils.html import escape
from django.utils.html import format_html
from django.utils.html import format_html_join
from django.utils.safestring import SafeString
from django.utils.safestring import mark_safe
from django.utils.text import Truncator

from camp.engine.rules.base_engine import BaseFeatureController
from camp.engine.rules.base_engine import CharacterController
from camp.engine.rules.base_engine import PropertyController
from camp.engine.rules.tempest.controllers.feature_controller import FeatureController
from camp.game.templatetags.markdown import render_markdown

register = template.Library()

# The element that feature / manage partials are swapped into.
MODAL_TARGET = "#characterModalContent"
TOOLTIP_DESCRIPTION_LENGTH = 200

# The rules engine links between features with relative markdown links,
# ex: "Granted by [Rogue](../rogue)."
_RELATIVE_FEATURE_HREF = re.compile(r'href="\.\./([^"]+)"')


@register.simple_tag(takes_context=True)
def get(context: dict, expr: str, attr: str | None = None) -> int:
    """Get a value from the character sheet.

    If no controller is specified, checks the current context for a CharacterController object
    called 'controller', 'character', or failing that, just checks everything.
    """
    controller = _find_context_controller(
        context, CharacterController, ("controller", "character")
    )
    if not controller:
        raise ValueError("No controller specified and no controller found in context.")
    if attr:
        return controller.get(f"{expr}.{attr}")
    return controller.get(expr)


@register.simple_tag(takes_context=True)
def subcon(
    context: dict, expr: str, controller: CharacterController | None = None
) -> PropertyController:
    """Get a value from the character sheet.

    If no controller is specified, checks the current context for a CharacterController object
    called 'controller', 'character', or failing that, just checks everything.
    """
    if not controller:
        controller = _find_context_controller(
            context, CharacterController, ("controller", "character")
        )
    if not controller:
        raise ValueError("No controller specified and no controller found in context.")
    return controller.controller(expr)


@register.simple_tag()
def name_without_tags(feature: FeatureController, *tags):
    tag_set = set()
    for t in tags:
        if isinstance(t, set):
            tag_set |= t
        elif t:
            tag_set.add(t)
    return feature.name_with_tags(exclude_tags=tag_set)


def feature_url(character, feature, **query) -> str:
    """URL of the feature view for a feature (controller or ID) on a character.

    Raises NoReverseMatch if the feature ID can't be represented in a URL.
    """
    character_id = getattr(character, "id", character)
    feature_id = getattr(feature, "full_id", feature)
    url = reverse("character-feature-view", args=[character_id, feature_id])
    if query := {k: v for k, v in query.items() if v}:
        url = f"{url}?{urlencode(query)}"
    return url


@register.simple_tag()
def modal_attrs(url: str) -> SafeString:
    """Attributes that load `url` into the character modal via HTMX.

    Pair this with a real `href` so the link still works without JavaScript,
    and when opened in a new tab.
    """
    return format_html(
        'hx-get="{}" hx-target="{}" data-character-modal-open', url, MODAL_TARGET
    )


@register.simple_tag()
def modal_form_attrs(url: str, in_modal: bool = False) -> SafeString:
    """Attributes for a form that posts to `url`.

    Inside the character modal, the response replaces the modal content.
    Anywhere else, it's an ordinary form submission.
    """
    attrs = format_html('method="post" action="{}"', url)
    if in_modal:
        attrs = format_html('{} hx-post="{}" hx-target="{}"', attrs, url, MODAL_TARGET)
    return attrs


@register.simple_tag()
def feature_link_attrs(character, feature, **query) -> SafeString:
    """`href` plus modal attributes for a link to a feature on a character sheet.

    `feature` may be a feature controller or a feature ID.
    """
    try:
        url = feature_url(character, feature, **query)
    except NoReverseMatch:
        return mark_safe("")  # nosec B308 B703: Constant string.
    return format_html('href="{}" {}', url, modal_attrs(url))


@register.filter()
def character_markdown(value, character) -> SafeString:
    """Render engine-provided markdown, pointing feature links at the character.

    Relative links like `../rogue` only resolve correctly from a feature page.
    They are rewritten (after sanitizing) to the feature view for the given
    character so they work from anywhere, and open in the character modal.
    """
    if not value:
        return mark_safe("")  # nosec B308 B703: Constant string.

    def rewrite(match: re.Match) -> str:
        target_id = unquote(html.unescape(match.group(1)))
        # An anchor without an href is rendered as plain text, which is
        # a better fallback than a link that goes nowhere useful.
        return feature_link_attrs(character, target_id)

    rendered = _RELATIVE_FEATURE_HREF.sub(rewrite, render_markdown(str(value)))
    # nh3 has sanitized the markdown output, and the replacement attributes
    # are escaped by format_html.
    return mark_safe(rendered)  # nosec B308 B703


@register.simple_tag(takes_context=True)
def feature_tooltip_attrs(
    context: dict,
    feature: BaseFeatureController | str | None,
    discount: int | str | None = None,
    type_label: str | None = None,
    name: str | None = None,
) -> SafeString:
    """Bootstrap tooltip attributes summarizing a feature.

    `feature` is normally a feature controller. A feature ID is also accepted
    (ex: for choices), and is looked up on the character controller in the context.

    The tooltip body is HTML, and it lives inside an HTML attribute, so it is
    escaped twice: once for each value in the body, and once for the whole body.
    """
    if isinstance(feature, str):
        name = name or feature
        feature = _lookup_feature(context, feature)

    cost = effect = description = None
    if isinstance(feature, BaseFeatureController):
        name = name or feature.display_name()
        cost = feature.purchase_cost_string()
        if card := getattr(feature, "power_card", lambda: None)():
            effect = card.effect
            description = card.description

    rows: list[tuple[str, object]] = []
    if type_label:
        rows.append(("Type", type_label))
    if discount:
        rows.append(("Discount", discount))
    if cost:
        rows.append(("Original Cost" if discount else "Cost", cost))
    if effect:
        rows.append(("Effect", effect))
    if description:
        rows.append(
            (
                "Description",
                Truncator(description).chars(TOOLTIP_DESCRIPTION_LENGTH),
            )
        )

    body = format_html(
        "<div class='tooltip-content'><strong>{}</strong>{}</div>",
        name or "",
        format_html_join("", "<br><strong>{}:</strong> {}", rows),
    )
    return format_html(
        'data-bs-toggle="tooltip" data-bs-html="true" data-bs-placement="top" '
        'data-bs-custom-class="skill-tooltip" data-bs-title="{}"',
        escape(body),
    )


def _lookup_feature(context: dict, feature_id: str) -> BaseFeatureController | None:
    controller = _find_context_controller(
        context, CharacterController, ("controller", "character")
    )
    if controller is None:
        return None
    try:
        return controller.feature_controller(feature_id)
    except (ValueError, LookupError):
        return None


_T = TypeVar("_T")


def _find_context_controller(
    context: template.RequestContext,
    controller_type: Type[_T],
    preferred_names: tuple[str],
) -> _T | None:
    """Find a controller of the given type in the context.

    If no controller is found, returns None.
    """
    for name in itertools.chain(preferred_names, (context.flatten().keys())):
        if controller := context.get(name):
            if isinstance(controller, controller_type):
                return controller
    return None
