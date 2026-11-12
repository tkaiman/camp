"""Tests for the reactive (HTMX) character sheet.

The sheet is progressively enhanced: every link and form works as an ordinary
page load or POST/redirect, and HTMX requests get partial responses that update
the page in place. Both are covered here.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.template import Context
from django.template import Template
from django.test import RequestFactory
from django.utils import timezone
from django_htmx.middleware import HtmxDetails

import camp.game.models
from camp.character import models
from camp.character import views
from camp.character.templatetags.character_sheet import character_markdown

HTMX = {"HX-Request": "true"}
BOOSTED = {"HX-Request": "true", "HX-Boosted": "true"}
MODAL_TARGET = 'hx-target="#characterModalContent"'
CLOSE_EVENT = "close-character-modal"

PARTIALS_DIR = (
    Path(views.__file__).parent / "templates" / "character" / "partials"
).resolve()


# ---------------------------------------------------------------- Fixtures


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="testuser")


@pytest.fixture
def other_user(django_user_model):
    return django_user_model.objects.create_user(username="someone-else")


@pytest.fixture
def game():
    game = camp.game.models.Game.objects.first()
    ruleset = game.rulesets.create(package="camp.tempest.v1")
    assert ruleset.ruleset.bad_defs == []
    return game


@pytest.fixture
def campaign(game):
    return camp.game.models.Campaign.objects.create(
        name="Test",
        slug="test",
        game=game,
        start_year=2023,
    )


@pytest.fixture
def character(game, user):
    """A freeplay character with plenty of levels and CP to spend."""
    character = models.Character.objects.create(
        name="Test",
        game=game,
        campaign=None,
        owner=user,
    )
    sheet = character.primary_sheet
    sheet.controller.xp_level = 10
    sheet.controller.freeplay_cp = 50
    sheet.save()
    return character


@pytest.fixture
def campaign_character(game, campaign, user):
    return models.Character.objects.create(
        name="Campaigner",
        game=game,
        campaign=campaign,
        owner=user,
    )


@pytest.fixture
def user_client(client, user):
    client.force_login(user)
    return client


# ----------------------------------------------------------------- Helpers


class _Element:
    def __init__(self, tag: str, attrs: dict[str, str | None], parent):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.text = ""

    @property
    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _Document(HTMLParser):
    """Just enough of a DOM to ask where elements are, and what they contain."""

    VOID = {"br", "hr", "img", "input", "link", "meta", "area", "base", "col"}

    def __init__(self, content: str):
        super().__init__(convert_charrefs=True)
        self.elements: list[_Element] = []
        self._open: list[_Element] = []
        self.feed(content)
        self.close()

    def handle_starttag(self, tag, attrs):
        parent = self._open[-1] if self._open else None
        element = _Element(tag, dict(attrs), parent)
        self.elements.append(element)
        if tag not in self.VOID:
            self._open.append(element)

    def handle_endtag(self, tag):
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i].tag == tag:
                del self._open[i:]
                break

    def handle_data(self, data):
        for element in self._open:
            element.text += data

    def by_id(self, id: str) -> list[_Element]:
        return [e for e in self.elements if e.attrs.get("id") == id]

    def one(self, id: str) -> _Element:
        (element,) = self.by_id(id)
        return element

    def find(self, tag: str, **attrs) -> list[_Element]:
        return [
            e
            for e in self.elements
            if e.tag == tag and all(e.attrs.get(k) == v for k, v in attrs.items())
        ]


def _body(response) -> str:
    return response.content.decode()


def _text(element: _Element) -> str:
    return " ".join(element.text.split())


def _triggers(response) -> dict:
    return json.loads(response.headers.get("HX-Trigger", "{}"))


def _feature_url(character, feature_id: str) -> str:
    return f"/characters/{character.id}/f/{feature_id}/"


def _value(character, expr: str) -> int:
    return models.Character.objects.get(pk=character.pk).primary_sheet.controller.get(
        expr
    )


def _assert_partial(response):
    """The response is a fragment for HTMX to swap in, with no page around it."""
    assert response.status_code == 200
    body = _body(response)
    assert "<html" not in body
    assert "<body" not in body
    # Client behavior belongs in static/js/base.js, not in swapped content.
    assert "<script" not in body


def _assert_sheet_updated(response) -> _Document:
    """The response replaces the header, summary, and messages out-of-band."""
    _assert_partial(response)
    doc = _Document(_body(response))
    assert doc.one("character-header").attrs["hx-swap-oob"] == "innerHTML"
    assert doc.one("character-summary").attrs["hx-swap-oob"] == "innerHTML"
    assert doc.one("messages").attrs["hx-swap-oob"] == "true"
    return doc


def _assert_modal_closed(response) -> _Document:
    """The sheet is updated, and the modal is told to close without new content."""
    doc = _assert_sheet_updated(response)
    assert CLOSE_EVENT in _triggers(response)
    assert response.headers["HX-Reswap"] == "none"
    assert not doc.by_id("characterModalLabel")
    return doc


def _assert_modal_shows(response, title: str) -> _Document:
    """The sheet is updated, and the modal stays open showing the named feature."""
    doc = _assert_sheet_updated(response)
    assert CLOSE_EVENT not in _triggers(response)
    assert "HX-Reswap" not in response.headers
    assert _text(doc.one("characterModalLabel")) == title
    # Modal content comes first, as the main swap. It isn't inside an OOB element.
    label = doc.one("characterModalLabel")
    assert not any("hx-swap-oob" in a.attrs for a in label.ancestors)
    return doc


def _undo_id(doc: _Document) -> str:
    (field,) = [
        e
        for e in doc.find("input", name="undo")
        if any(a.attrs.get("id") == "character-header" for a in e.ancestors)
    ]
    return field.attrs["value"]


def _purchase(client, character, feature_id: str, headers=None, **data):
    return client.post(
        _feature_url(character, feature_id),
        {"purchase": "", **data},
        headers=headers,
    )


# ------------------------------------------------- Classic (non-HTMX) flows


@pytest.mark.django_db
def test_sheet_is_rendered_complete_on_first_load(user_client, character):
    _purchase(user_client, character, "mage", ranks=2)

    response = user_client.get(f"/characters/{character.id}/")

    assert response.status_code == 200
    body = _body(response)
    assert "<html" in body
    doc = _Document(body)
    header = doc.one("character-header")
    summary = doc.one("character-summary")
    modal = doc.one("characterModal")
    doc.one("characterModalContent")
    doc.one("messages")
    # The summary is there from the start, rather than being fetched afterwards.
    assert "hx-trigger" not in summary.attrs
    assert "hx-swap-oob" not in summary.attrs
    assert "Mage" in summary.text
    assert "Level 2" in header.text
    # The modal survives the header and summary being replaced.
    assert header not in modal.ancestors
    assert summary not in modal.ancestors
    assert "modal-dialog-scrollable" in body
    # Available features are fetched on demand, not rendered into the page.
    assert "newFeatureModal_" not in body
    assert "hx-preserve" not in body
    add_url = f"/characters/{character.id}/available/skill/"
    (add_link,) = doc.find("a", href=add_url)
    assert add_link.attrs["hx-get"] == add_url
    assert add_link.attrs["hx-target"] == "#characterModalContent"
    assert "data-character-modal-open" in add_link.attrs
    # Nothing toggles a modal and a tooltip from the same element.
    assert 'data-bs-toggle="modal"' not in body


@pytest.mark.django_db
def test_feature_page_without_htmx(user_client, character):
    response = user_client.get(_feature_url(character, "mage"))

    assert response.status_code == 200
    body = _body(response)
    assert "<html" in body
    doc = _Document(body)
    form = doc.one("purchaseForm")
    assert form.attrs["action"] == _feature_url(character, "mage")
    assert form.attrs["method"] == "post"
    # Forms on the page itself submit normally.
    assert "hx-post" not in form.attrs
    assert f"Back to {character}" in body
    # The page has no modal of its own.
    assert not doc.by_id("characterModal")


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [None, BOOSTED], ids=["plain", "boosted"])
def test_posts_without_htmx_redirect(user_client, character, headers):
    # Choices to make: stay on the feature.
    response = _purchase(user_client, character, "mage", headers, ranks=2)
    assert response.status_code == 302
    assert response.url == _feature_url(character, "mage")

    # Choices always return to the feature.
    response = user_client.post(
        _feature_url(character, "mage"),
        {"choice": "magical-specialty", "selection": "mage-magical-specialty-3"},
        headers=headers,
    )
    assert response.status_code == 302
    assert response.url == _feature_url(character, "mage")

    # Nothing more to do: back to the sheet.
    response = _purchase(user_client, character, "basic-medicine", headers)
    assert response.status_code == 302
    assert response.url == f"/characters/{character.id}/"
    assert _value(character, "basic-medicine") == 1

    response = user_client.post(
        _feature_url(character, "basic-medicine"), {"remove": "1"}, headers=headers
    )
    assert response.status_code == 302
    assert response.url == f"/characters/{character.id}/"
    assert _value(character, "basic-medicine") == 0

    # Things to purchase within the feature: stay on it.
    response = _purchase(user_client, character, "dryad", headers)
    assert response.status_code == 302
    assert response.url == _feature_url(character, "dryad")

    # Purchases within a feature return to it.
    response = _purchase(user_client, character, "woodland-born", headers)
    assert response.status_code == 302
    assert response.url == _feature_url(character, "dryad")


@pytest.mark.django_db
def test_boosted_get_returns_full_page(user_client, character):
    response = user_client.get(_feature_url(character, "mage"), headers=BOOSTED)

    assert response.status_code == 200
    assert "<html" in _body(response)


@pytest.mark.django_db
def test_manage_actions_without_htmx_redirect(user_client, character):
    sheet_url = f"/characters/{character.id}/"

    response = user_client.post(f"{sheet_url}set/", {"level": "5", "cp": "3"})
    assert (response.status_code, response.url) == (302, sheet_url)

    response = user_client.post(f"{sheet_url}name/", {"name": "Renamed"})
    assert (response.status_code, response.url) == (302, sheet_url)

    response = user_client.post(
        f"{sheet_url}apply/", {"mutation": '{"type": "respend"}'}
    )
    assert (response.status_code, response.url) == (302, sheet_url)

    response = user_client.post(f"{sheet_url}undo/", {"undo": "bogus"})
    assert (response.status_code, response.url) == (302, sheet_url)

    response = user_client.post(f"{sheet_url}copy/", {"name": "Copy"})
    copy = models.Character.objects.get(name="Copy")
    assert (response.status_code, response.url) == (302, f"/characters/{copy.id}/")

    response = user_client.post(f"{sheet_url}delete/")
    assert (response.status_code, response.url) == (302, "/")
    assert not models.Character.objects.filter(pk=character.pk).exists()


# ---------------------------------------------------------- Modal content


@pytest.mark.django_db
def test_htmx_feature_get_returns_modal_partial(user_client, character):
    url = _feature_url(character, "mage")

    response = user_client.get(url, headers=HTMX)

    _assert_partial(response)
    body = _body(response)
    assert 'class="modal-header"' in body
    assert 'class="modal-body"' in body
    doc = _Document(body)
    assert _text(doc.one("characterModalLabel")) == "Mage"
    form = doc.one("purchaseForm")
    # In the modal, the form replaces the modal content. It also still
    # says where it goes, since the page it's on is the character sheet.
    assert form.attrs["action"] == url
    assert form.attrs["hx-post"] == url
    assert form.attrs["hx-target"] == "#characterModalContent"
    # Just opening a feature doesn't touch the rest of the page.
    assert "hx-swap-oob" not in body


@pytest.mark.django_db
def test_htmx_feature_get_unknown_feature(user_client, character):
    response = user_client.get(_feature_url(character, "no-such-thing"), headers=HTMX)

    assert response.status_code == 404


@pytest.mark.django_db
def test_available_group(user_client, character):
    url = f"/characters/{character.id}/available/skill/"

    response = user_client.get(url, headers=HTMX)

    _assert_partial(response)
    body = _body(response)
    doc = _Document(body)
    assert _text(doc.one("characterModalLabel")) == "Add New Skills"
    # Features open in the same modal, and remember where they were found.
    feature_url = f"{_feature_url(character, 'basic-medicine')}?group=skill"
    (link,) = doc.find("a", href=feature_url)
    assert link.attrs["hx-get"] == feature_url
    assert link.attrs["hx-target"] == "#characterModalContent"
    assert "Basic Medicine" in link.text
    # ...and describe themselves on hover.
    assert link.attrs["data-bs-toggle"] == "tooltip"
    assert link.attrs["data-bs-custom-class"] == "skill-tooltip"
    assert "<strong>Basic Medicine</strong>" in link.attrs["data-bs-title"]
    assert "<strong>Cost:</strong> 2 CP" in link.attrs["data-bs-title"]
    # Only this group is listed.
    assert not doc.find("a", href=f"{_feature_url(character, 'mage')}?group=class")
    assert "hx-preserve" not in body
    # Categories are collapsed sections with IDs that can't collide.
    collapses = [
        e.attrs["id"]
        for e in doc.elements
        if "accordion-collapse" in (e.attrs.get("class") or "")
    ]
    assert len(collapses) > 1
    assert len(collapses) == len(set(collapses))
    assert all(c.startswith("collapse-skill-") for c in collapses)


@pytest.mark.django_db
def test_available_group_links_back_from_feature(user_client, character):
    group_url = f"/characters/{character.id}/available/skill/"
    feature_url = _feature_url(character, "basic-medicine")

    response = user_client.get(f"{feature_url}?group=skill", headers=HTMX)

    _assert_partial(response)
    doc = _Document(_body(response))
    (back,) = doc.find("a", href=group_url)
    assert "Back to Skills" in back.text
    assert back.attrs["hx-get"] == group_url
    assert back.attrs["hx-target"] == "#characterModalContent"
    # The form keeps the group, so the link is still there if it comes back with errors.
    assert doc.one("purchaseForm").attrs["hx-post"] == f"{feature_url}?group=skill"

    # A group that the feature isn't in is ignored.
    response = user_client.get(f"{feature_url}?group=class", headers=HTMX)
    assert "Back to" not in _body(response)
    response = user_client.get(feature_url, headers=HTMX)
    assert "Back to" not in _body(response)


@pytest.mark.django_db
def test_available_group_without_htmx_is_a_page(user_client, character):
    response = user_client.get(f"/characters/{character.id}/available/skill/")

    assert response.status_code == 200
    body = _body(response)
    assert "<html" in body
    assert "Add New Skills" in body
    assert f"{_feature_url(character, 'basic-medicine')}?group=skill" in body
    doc = _Document(body)
    assert len(doc.by_id("character-header")) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("headers", [None, HTMX], ids=["plain", "htmx"])
def test_available_group_not_found(client, user_client, other_user, character, headers):
    base = f"/characters/{character.id}/available"
    # Not a group at all.
    assert user_client.get(f"{base}/nonsense/", headers=headers).status_code == 404
    # A real type, but nothing in it can be added (no class grants powers yet).
    assert user_client.get(f"{base}/power/", headers=headers).status_code == 404
    # No such character.
    response = user_client.get("/characters/99999/available/skill/", headers=headers)
    assert response.status_code == 404
    # Not your character.
    client.force_login(other_user)
    assert client.get(f"{base}/skill/", headers=headers).status_code == 404
    # Not logged in.
    client.logout()
    assert client.get(f"{base}/skill/", headers=headers).status_code == 404


@pytest.mark.django_db
def test_feature_view_requires_permission(client, other_user, character):
    client.force_login(other_user)

    response = client.get(_feature_url(character, "mage"), headers=HTMX)
    assert response.status_code == 403

    response = _purchase(client, character, "mage", HTMX, ranks=2)
    assert response.status_code == 403
    assert _value(character, "mage") == 0


# ------------------------------------------------------ Feature mutations


@pytest.mark.django_db
def test_htmx_purchase_and_rank_updates_keep_modal_open_for_choices(
    user_client, character
):
    response = _purchase(user_client, character, "mage", HTMX, ranks=2)

    doc = _assert_modal_shows(response, "Mage")
    assert _value(character, "mage") == 2
    header = doc.one("character-header")
    assert "Level 2" in header.text
    assert "Arcane Slots" in header.text
    assert "Mage" in doc.one("character-summary").text
    messages = doc.one("messages").text
    assert "Mage x2 purchased." in messages
    assert "Choices are available for this feature." in messages
    # The modal is showing the feature as it is now, ready for its choices.
    assert doc.find("select", name="selection")
    (modal_body,) = [e for e in doc.elements if e.attrs.get("class") == "modal-body"]
    assert "Mage x2 purchased." in modal_body.text
    # The undo entry for this change is ready to use.
    assert _undo_id(doc) == str(character.primary_sheet.last_undo.id)

    # Adding ranks.
    response = _purchase(user_client, character, "mage", HTMX, ranks=4)
    doc = _assert_modal_shows(response, "Mage")
    assert _value(character, "mage") == 4
    assert "Level 4" in doc.one("character-header").text
    assert "Mage x2 purchased." in doc.one("messages").text

    # Refunding ranks.
    response = _purchase(user_client, character, "mage", HTMX, ranks=3)
    doc = _assert_modal_shows(response, "Mage")
    assert _value(character, "mage") == 3
    assert "Level 3" in doc.one("character-header").text
    assert "Mage x1 refunded." in doc.one("messages").text


@pytest.mark.django_db
def test_htmx_simple_purchase_and_removal_close_modal(user_client, character):
    cp = _value(character, "cp")

    response = _purchase(user_client, character, "basic-medicine", HTMX)

    doc = _assert_modal_closed(response)
    assert _value(character, "basic-medicine") == 1
    assert f"CP: {cp - 2}" in doc.one("character-header").text
    assert "Basic Medicine" in doc.one("character-summary").text
    assert "Basic Medicine x1 purchased." in doc.one("messages").text
    # The link to the new feature opens it in the modal, with a tooltip.
    url = _feature_url(character, "basic-medicine")
    (link,) = doc.find("a", href=url)
    assert link.attrs["hx-get"] == url
    assert link.attrs["data-bs-toggle"] == "tooltip"

    # Once taken, a single-rank feature offers a remove button instead.
    response = user_client.get(url, headers=HTMX)
    assert _Document(_body(response)).one("removeButton").attrs["name"] == "remove"

    response = user_client.post(url, {"remove": "1"}, headers=HTMX)

    doc = _assert_modal_closed(response)
    assert _value(character, "basic-medicine") == 0
    assert f"CP: {cp}" in doc.one("character-header").text
    assert not doc.find("a", href=url)
    assert "Basic Medicine x1 refunded." in doc.one("messages").text


@pytest.mark.django_db
def test_htmx_internal_subfeature_returns_to_parent(user_client, character):
    # A breed has things to purchase within it, so the modal stays on it.
    response = _purchase(user_client, character, "dryad", HTMX)
    doc = _assert_modal_shows(response, "Dryad")
    assert "Purchases are available within this feature." in doc.one("messages").text
    child_url = _feature_url(character, "woodland-born")
    assert doc.find("a", href=child_url)

    # Having purchased one of them, go back to the breed for the next.
    response = _purchase(user_client, character, "woodland-born", HTMX)
    doc = _assert_modal_shows(response, "Dryad")
    assert _value(character, "woodland-born") == 1
    assert "Woodland Born x1 purchased." in doc.one("messages").text
    assert "Woodland Born" in doc.one("character-summary").text
    # The breed's own form posts to the breed, not the feature just purchased.
    assert doc.one("purchaseForm").attrs["hx-post"] == _feature_url(character, "dryad")

    # Likewise when removing it.
    response = user_client.post(child_url, {"remove": "1"}, headers=HTMX)
    doc = _assert_modal_shows(response, "Dryad")
    assert _value(character, "woodland-born") == 0
    assert "Woodland Born" not in doc.one("character-summary").text


@pytest.mark.django_db
def test_feature_that_vanishes_when_removed(user_client, character, monkeypatch):
    """Some features leave no controller behind once their last rank is removed."""
    _purchase(user_client, character, "dryad")
    _purchase(user_client, character, "woodland-born")
    _purchase(user_client, character, "basic-medicine")
    find_feature = views._find_feature

    def find_unless_removed(controller, feature_id):
        feature = find_feature(controller, feature_id)
        if feature_id in vanishing and feature is not None and feature.value == 0:
            return None
        return feature

    vanishing = {"woodland-born", "basic-medicine"}
    monkeypatch.setattr(views, "_find_feature", find_unless_removed)

    # What's left of a feature inside another is that other feature.
    response = user_client.post(
        _feature_url(character, "woodland-born"), {"remove": "1"}, headers=HTMX
    )
    doc = _assert_modal_shows(response, "Dryad")
    assert _value(character, "woodland-born") == 0
    assert "Woodland Born x1 refunded." in doc.one("messages").text

    # Otherwise, there's nothing left to show.
    response = user_client.post(
        _feature_url(character, "basic-medicine"), {"remove": "1"}
    )
    assert response.status_code == 302
    assert response.url == f"/characters/{character.id}/"
    assert _value(character, "basic-medicine") == 0


@pytest.mark.django_db
def test_htmx_choices(user_client, character):
    url = _feature_url(character, "mage")
    _purchase(user_client, character, "mage", ranks=2)
    choice = {"choice": "magical-specialty", "selection": "mage-magical-specialty-3"}

    # Making the only choice leaves nothing more to do.
    response = user_client.post(url, choice, headers=HTMX)
    doc = _assert_modal_closed(response)
    assert _value(character, "mage-magical-specialty-3") == 1
    assert "Bookcasting Specialist" in doc.one("character-summary").text
    assert "Choice applied." in doc.one("messages").text

    # The choice is listed on the feature, linked, and removable.
    response = user_client.get(url, headers=HTMX)
    doc = _Document(_body(response))
    links = doc.find("a", href=_feature_url(character, "mage-magical-specialty-3"))
    assert any(
        "<strong>Type:</strong> Choice Selection" in a.attrs["data-bs-title"]
        for a in links
    )
    assert doc.find("button", name="selection", value="mage-magical-specialty-3")

    # Removing it means there's a choice to make again, so stay.
    response = user_client.post(url, {**choice, "unchoose": "1"}, headers=HTMX)
    doc = _assert_modal_shows(response, "Mage")
    assert _value(character, "mage-magical-specialty-3") == 0
    assert "Bookcasting Specialist" not in doc.one("character-summary").text
    (select,) = doc.find("select", name="selection")
    # Choice fields have IDs of their own, so labels can't get crossed.
    assert select.attrs["id"] == "id_magical-specialty_selection"


@pytest.mark.django_db
def test_htmx_rejected_mutations_rerender_modal_only(user_client, character):
    url = _feature_url(character, "lore")

    # The custom option goes with an "Other" choice among the listed options.
    response = user_client.get(url, headers=HTMX)
    doc = _Document(_body(response))
    assert doc.find("input", name="option", value="__other__")
    assert doc.one("id_option_freeform").attrs["name"] == "option_freeform"

    # Form validation.
    response = _purchase(
        user_client, character, "lore", HTMX, option="__other__", option_freeform="No*"
    )
    _assert_partial(response)
    body = _body(response)
    doc = _Document(body)
    assert _text(doc.one("characterModalLabel")) == "Lore"
    assert "is not a valid option" in body
    # The submission is kept for another try.
    assert doc.one("id_option_freeform").attrs["value"] == "No*"
    # Messages are replaced, but the sheet behind the modal is left as it is.
    assert doc.one("messages").attrs["hx-swap-oob"] == "true"
    assert not doc.by_id("character-header")
    assert not doc.by_id("character-summary")
    assert CLOSE_EVENT not in _triggers(response)
    assert _value(character, "lore") == 0

    # Rejected by the rules engine: a first class can't be taken at level 1.
    response = _purchase(user_client, character, "mage", HTMX, ranks=1)
    _assert_partial(response)
    doc = _Document(_body(response))
    assert _text(doc.one("characterModalLabel")) == "Mage"
    assert not doc.by_id("character-header")
    assert CLOSE_EVENT not in _triggers(response)
    assert _value(character, "mage") == 0

    # A choice with nothing chosen.
    _purchase(user_client, character, "mage", ranks=2)
    response = user_client.post(
        _feature_url(character, "mage"), {"choice": "magical-specialty"}, headers=HTMX
    )
    _assert_partial(response)
    doc = _Document(_body(response))
    assert "No selection made." in doc.one("messages").text
    assert _text(doc.one("characterModalLabel")) == "Mage"
    assert not doc.by_id("character-summary")
    assert CLOSE_EVENT not in _triggers(response)


@pytest.mark.django_db
def test_htmx_custom_option_purchase(user_client, character):
    response = _purchase(
        user_client,
        character,
        "lore",
        HTMX,
        option="__other__",
        option_freeform="Arts/Crafts",
    )

    doc = _assert_sheet_updated(response)
    assert _value(character, "lore+Arts/Crafts") == 1
    # Slashes in feature IDs are encoded so they fit in a URL path segment.
    url = f"/characters/{character.id}/f/lore+Arts$Crafts/"
    (link,) = doc.find("a", href=url)
    assert link.attrs["hx-get"] == url
    # ...and that URL leads back to the same feature.
    response = user_client.get(url, headers=HTMX)
    _assert_partial(response)
    assert "Lore [Arts/Crafts]" in _text(
        _Document(_body(response)).one("characterModalLabel")
    )


@pytest.mark.django_db
def test_htmx_discarded_character_is_not_updated(user_client, campaign_character):
    campaign_character.discarded_date = timezone.now()
    campaign_character.save()
    base = f"/characters/{campaign_character.id}"

    for response in (
        user_client.get(f"{base}/f/mage/", headers=HTMX),
        user_client.post(f"{base}/f/mage/", {"purchase": "", "ranks": 2}, headers=HTMX),
        user_client.post(f"{base}/name/", {"name": "Back Again"}, headers=HTMX),
        user_client.post(
            f"{base}/apply/", {"mutation": '{"type": "respend"}'}, headers=HTMX
        ),
        user_client.post(f"{base}/undo/", {"undo": "1"}, headers=HTMX),
    ):
        # No redirect for HTMX to follow into the modal. The sheet explains instead.
        doc = _assert_modal_closed(response)
        assert "Can't update discarded character." in doc.one("messages").text
        assert "Campaigner (Discarded)" in doc.one("character-header").text
    assert _value(campaign_character, "mage") == 0


# ----------------------------------------------------- Feature link markup


def test_character_markdown_rewrites_feature_links():
    character = SimpleNamespace(id=7)

    html = character_markdown("Granted by [Rogue](../rogue).", character)

    doc = _Document(html)
    (link,) = doc.find("a")
    assert link.text == "Rogue"
    assert link.attrs["href"] == "/characters/7/f/rogue/"
    assert link.attrs["hx-get"] == "/characters/7/f/rogue/"
    assert link.attrs["hx-target"] == "#characterModalContent"
    assert "data-character-modal-open" in link.attrs
    assert "Granted by" in html
    # A character ID works as well as a character.
    assert 'href="/characters/7/f/rogue/"' in character_markdown("[R](../rogue)", 7)


@pytest.mark.parametrize(
    "target,expected",
    [
        ("devotion/patron", "/characters/7/f/devotion$patron/"),
        ("lore+Sea_Shanties", "/characters/7/f/lore+Sea_Shanties/"),
        ("lore+Arts/Crafts", "/characters/7/f/lore+Arts$Crafts/"),
        ("lore+Arts%2FCrafts", "/characters/7/f/lore+Arts$Crafts/"),
        ("lore+R&D", "/characters/7/f/lore+R&D/"),
        ("lore+Who?", "/characters/7/f/lore+Who%3F/"),
        ("weapon-specialization+Axe", "/characters/7/f/weapon-specialization+Axe/"),
    ],
)
def test_character_markdown_encodes_feature_ids(target, expected):
    html = character_markdown(f"Extends [Source](../{target})", 7)

    (link,) = _Document(html).find("a")
    assert link.attrs["href"] == expected
    assert link.attrs["hx-get"] == expected
    assert link.attrs["hx-target"] == "#characterModalContent"


def test_character_markdown_leaves_other_content_alone():
    html = character_markdown(
        "See [the rules](https://example.com/rules) and [home](/). "
        "<script>alert(1)</script> [x](javascript:alert(1))",
        7,
    )

    doc = _Document(html)
    hrefs = [a.attrs.get("href") for a in doc.find("a")]
    assert hrefs == ["https://example.com/rules", "/", None]
    assert not any("hx-get" in a.attrs for a in doc.find("a"))
    assert "<script" not in html
    assert character_markdown("", 7) == ""
    assert character_markdown(None, 7) == ""


def test_character_markdown_falls_back_when_feature_has_no_url():
    # Not something that can appear in a feature URL.
    html = character_markdown("Part of [Thing](../bad*id).", 7)

    (link,) = _Document(html).find("a")
    assert link.text == "Thing"
    assert "href" not in link.attrs
    assert "hx-get" not in link.attrs


def test_character_markdown_does_not_carry_state_between_calls():
    # Reference definitions would otherwise leak from one document into the next.
    character_markdown("[one][ref]\n\n[ref]: ../rogue", 7)

    html = character_markdown("[two][ref]", 7)

    assert "<a" not in html


@pytest.mark.django_db
def test_feature_explanations_link_within_the_modal(user_client, character):
    _purchase(user_client, character, "mage", ranks=2)

    response = user_client.get(_feature_url(character, "basic-arcane"), headers=HTMX)

    doc = _Document(_body(response))
    url = _feature_url(character, "mage")
    links = [a for a in doc.find("a", href=url) if "Granted by" in a.parent.text]
    assert links
    assert links[0].attrs["hx-get"] == url
    assert links[0].attrs["hx-target"] == "#characterModalContent"
    # Relative links only ever worked from the feature page itself.
    assert 'href="../' not in _body(response)


def test_feature_tooltip_escapes_markup_and_quotes():
    template = Template(
        "{% load character_sheet %}"
        '<a {% feature_tooltip_attrs "some-id" name=name type_label=label discount=3 %}>x</a>'
    )

    html = template.render(Context({"name": 'Say "hi" <b>&', "label": "It's <i>"}))

    # The attribute survives parsing intact, with the values as text inside it.
    (link,) = _Document(html).find("a")
    assert link.attrs["data-bs-toggle"] == "tooltip"
    assert link.attrs["data-bs-html"] == "true"
    assert link.attrs["data-bs-custom-class"] == "skill-tooltip"
    assert link.attrs["data-bs-title"] == (
        "<div class='tooltip-content'>"
        "<strong>Say &quot;hi&quot; &lt;b&gt;&amp;</strong>"
        "<br><strong>Type:</strong> It&#x27;s &lt;i&gt;"
        "<br><strong>Discount:</strong> 3"
        "</div>"
    )
    assert set(link.attrs) == {
        "data-bs-toggle",
        "data-bs-html",
        "data-bs-placement",
        "data-bs-custom-class",
        "data-bs-title",
    }


@pytest.mark.django_db
def test_sheet_survives_awkward_character_names(user_client, character):
    character.name = '"Quote" <b>O\'Brien</b> & Co'
    character.save()

    response = user_client.get(f"/characters/{character.id}/")

    doc = _Document(_body(response))
    assert '"Quote" <b>O\'Brien</b> & Co' in doc.one("character-header").text
    assert not doc.find("b")


def test_partials_have_no_scripts():
    partials = list(PARTIALS_DIR.glob("*.html"))
    assert len(partials) >= 5
    for path in partials:
        content = path.read_text()
        assert "<script" not in content, path.name
        assert "htmx.onLoad" not in content, path.name
        assert "hx-preserve" not in content, path.name


def test_feature_groups_have_distinct_keys():
    """Subfeatures share a feature type, but their groups must not share DOM IDs."""

    def subfeature(id: str, type_name: str, category: str | None = None):
        return SimpleNamespace(
            full_id=id,
            internal=True,
            parent=None,
            feature_type="subfeature",
            type_name=type_name,
            should_show_in_list=False,
            is_option_template=False,
            category=category,
            category_priority=0,
            sort_key=lambda: id,
        )

    controller = SimpleNamespace(
        plural_name=lambda name: f"{name}s", display_priority=lambda name: 1
    )
    skill = subfeature("a-skill", "ignored")
    skill.feature_type = "skill"

    groups = views._features(
        controller,
        [
            subfeature("path-1", "Path", "Tier 1"),
            subfeature("path-2", "Path", "Tier 2"),
            subfeature("spec-1", "Class Specialization"),
            skill,
        ],
        hide_internal=False,
    )

    assert [(g.name, g.type, g.key) for g in groups] == [
        ("Class Specializations", "subfeature", "subfeature-class-specialization"),
        ("Paths", "subfeature", "subfeature-path"),
        ("skills", "skill", "skill"),
    ]


# -------------------------------------------------------- Manage actions


@pytest.mark.django_db
def test_htmx_manage_forms(user_client, other_user, character, campaign_character):
    base = f"/characters/{character.id}/manage"

    for action, post_url in {
        "attrs": f"/characters/{character.id}/set/",
        "name": f"/characters/{character.id}/name/",
        "delete": f"/characters/{character.id}/delete/",
        "copy": f"/characters/{character.id}/copy/",
    }.items():
        response = user_client.get(f"{base}/{action}/", headers=HTMX)
        _assert_partial(response)
        doc = _Document(_body(response))
        doc.one("characterModalLabel")
        (form,) = doc.find("form", action=post_url)
        assert form.attrs["hx-post"] == post_url
        assert form.attrs["hx-target"] == "#characterModalContent"

        # Followed as a plain link, the same form is a page.
        response = user_client.get(f"{base}/{action}/")
        assert response.status_code == 200
        body = _body(response)
        assert "<html" in body
        (form,) = _Document(body).find("form", action=post_url)
        assert "hx-post" not in form.attrs

    # Forms show the current values.
    response = user_client.get(f"{base}/attrs/", headers=HTMX)
    doc = _Document(_body(response))
    assert doc.one("set-level").attrs["value"] == "10"
    assert doc.one("set-cp").attrs["value"] == "50"

    assert user_client.get(f"{base}/nonsense/", headers=HTMX).status_code == 404
    # Attributes are only set directly on freeplay characters.
    response = user_client.get(
        f"/characters/{campaign_character.id}/manage/attrs/", headers=HTMX
    )
    assert response.status_code == 404
    user_client.force_login(other_user)
    assert user_client.get(f"{base}/name/", headers=HTMX).status_code == 403


@pytest.mark.django_db
def test_htmx_set_attr(user_client, character):
    url = f"/characters/{character.id}/set/"

    response = user_client.post(url, {"level": "5", "cp": "20"}, headers=HTMX)

    doc = _assert_modal_closed(response)
    controller = models.Character.objects.get(pk=character.pk).primary_sheet.controller
    assert controller.xp_level == 5
    assert controller.freeplay_cp == 20
    messages = doc.one("messages").text
    assert "Level set to 5." in messages
    assert "Awarded CP set to 20." in messages
    assert f"CP: {controller.get('cp')}" in doc.one("character-header").text
    # The breakdown in the CP tooltip is current too.
    (cp_item,) = [
        e
        for e in doc.find("li")
        if "Freeplay: 20" in (e.attrs.get("data-bs-title") or "")
    ]
    assert cp_item.attrs["data-bs-toggle"] == "tooltip"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data,error",
    [
        ({"level": "lots", "cp": "20"}, "Level must be an integer."),
        ({"level": "5", "cp": "1.5"}, "Awarded CP must be an integer."),
    ],
)
def test_htmx_set_attr_invalid(user_client, character, data, error):
    response = user_client.post(f"/characters/{character.id}/set/", data, headers=HTMX)

    # Not a redirect, which HTMX would follow and load the sheet into the modal.
    doc = _assert_modal_closed(response)
    assert error in doc.one("messages").text
    controller = models.Character.objects.get(pk=character.pk).primary_sheet.controller
    assert controller.xp_level == 10
    assert controller.freeplay_cp == 50


@pytest.mark.django_db
def test_htmx_set_name(user_client, character):
    url = f"/characters/{character.id}/name/"

    response = user_client.post(url, {"name": " Sir <Robin> "}, headers=HTMX)

    doc = _assert_modal_closed(response)
    character.refresh_from_db()
    assert character.name == "Sir <Robin>"
    header = doc.one("character-header")
    (title,) = [e for e in doc.find("h1") if header in e.ancestors]
    assert _text(title) == "Sir <Robin>"
    assert "Name changed to Sir <Robin>" in doc.one("messages").text

    response = user_client.post(url, {"name": "   "}, headers=HTMX)

    doc = _assert_modal_closed(response)
    assert "Character name can't be empty." in doc.one("messages").text
    character.refresh_from_db()
    assert character.name == "Sir <Robin>"


@pytest.mark.django_db
def test_htmx_undo_uses_fresh_undo_ids(user_client, character):
    undo_url = f"/characters/{character.id}/undo/"
    first = _purchase(user_client, character, "basic-medicine", HTMX)
    first_id = _undo_id(_assert_modal_closed(first))
    second = _purchase(user_client, character, "fence", HTMX)
    doc = _assert_modal_closed(second)
    second_id = _undo_id(doc)
    assert first_id != second_id
    # The menu says what will be undone.
    assert "Undo Purchase Fence" in doc.one("character-header").text
    assert _value(character, "fence") == 1

    # An ID from before the last change is refused.
    response = user_client.post(undo_url, {"undo": first_id}, headers=HTMX)
    doc = _assert_modal_closed(response)
    assert "Invalid undo request." in doc.one("messages").text
    assert _value(character, "fence") == 1
    assert _undo_id(doc) == second_id

    # The ID that came with the latest update works...
    response = user_client.post(undo_url, {"undo": second_id}, headers=HTMX)
    doc = _assert_modal_closed(response)
    assert "Undid" in doc.one("messages").text
    assert _value(character, "fence") == 0
    assert _value(character, "basic-medicine") == 1
    assert "Fence" not in doc.one("character-summary").text
    assert "Basic Medicine" in doc.one("character-summary").text
    # ...and its response carries the next one, with no reload in between.
    assert _undo_id(doc) == first_id
    assert "Undo Purchase Basic Medicine" in doc.one("character-header").text

    response = user_client.post(undo_url, {"undo": first_id}, headers=HTMX)
    doc = _assert_modal_closed(response)
    assert _value(character, "basic-medicine") == 0
    assert "Basic Medicine" not in doc.one("character-summary").text
    header = doc.one("character-header")
    assert "Undo not available" in header.text
    assert not doc.find("input", name="undo")

    # Nothing left to undo.
    response = user_client.post(undo_url, {"undo": first_id}, headers=HTMX)
    doc = _assert_modal_closed(response)
    assert "Nothing to undo." in doc.one("messages").text


@pytest.mark.django_db
def test_htmx_respend(user_client, character):
    _purchase(user_client, character, "mage", ranks=2)
    _purchase(user_client, character, "basic-medicine")
    response = user_client.get(f"/characters/{character.id}/")
    # The menu offers the respend as a form of its own.
    apply_url = f"/characters/{character.id}/apply/"
    (form,) = _Document(_body(response)).find("form", action=apply_url)
    assert form.attrs["hx-post"] == apply_url
    assert form.attrs["hx-swap"] == "none"
    assert "hx-confirm" in form.attrs

    response = user_client.post(
        apply_url, {"mutation": '{"type": "respend"}'}, headers=HTMX
    )

    doc = _assert_modal_closed(response)
    assert _value(character, "mage") == 0
    assert _value(character, "basic-medicine") == 0
    summary = doc.one("character-summary").text
    assert "Mage" not in summary
    assert "Basic Medicine" not in summary
    assert "Level 0" in doc.one("character-header").text
    # The respend itself can be undone.
    assert _undo_id(doc) == str(character.primary_sheet.last_undo.id)


@pytest.mark.django_db
def test_htmx_copy_redirects_to_copy(user_client, character):
    _purchase(user_client, character, "basic-medicine")

    response = user_client.post(
        f"/characters/{character.id}/copy/", {"name": "Twin"}, headers=HTMX
    )

    twin = models.Character.objects.get(name="Twin")
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == f"/characters/{twin.id}/"
    assert _value(twin, "basic-medicine") == 1


@pytest.mark.django_db
def test_htmx_delete_redirects_home(user_client, character, campaign_character):
    response = user_client.post(f"/characters/{character.id}/delete/", headers=HTMX)

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/"
    assert not models.Character.objects.filter(pk=character.pk).exists()

    # Campaign characters are discarded rather than deleted.
    response = user_client.post(
        f"/characters/{campaign_character.id}/delete/", headers=HTMX
    )

    assert response.headers["HX-Redirect"] == "/"
    campaign_character.refresh_from_db()
    assert campaign_character.is_discarded


# ------------------------------------------------------------- View helpers


def test_is_htmx():
    factory = RequestFactory()

    def request(headers: dict | None, middleware: bool = True):
        request = factory.get("/", headers=headers)
        if middleware:
            request.htmx = HtmxDetails(request)
        return request

    assert views._is_htmx(request(HTMX)) is True
    # Boosted requests are navigation, and get whole pages.
    assert views._is_htmx(request(BOOSTED)) is False
    assert views._is_htmx(request(None)) is False
    # Without the middleware, nothing is treated as HTMX.
    assert views._is_htmx(request(HTMX, middleware=False)) is False


def test_partial_urls_have_no_marker_parameters():
    for path in [*PARTIALS_DIR.glob("*.html"), Path(views.__file__)]:
        assert not re.search(r"[?&]partial=", path.read_text()), path.name
