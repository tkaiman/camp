from __future__ import annotations

import markdown as md
import nh3
from django import template
from django.template.defaultfilters import stringfilter
from django.utils.safestring import mark_safe

register = template.Library()

_MD = md.Markdown(
    output="html",
    extensions=["tables", "smarty", "attr_list", "admonition"],
)


_ATTRIBUTES = {
    "a": {"target", "href", "name"},
}


def render_markdown(value: str, p_class: str | None = None) -> str:
    """Convert markdown to sanitized HTML.

    The shared Markdown instance keeps per-document state (references,
    footnotes, etc.), so it is reset before every conversion.
    """
    attr_values = {"p": {"class": p_class}} if p_class else None
    _MD.reset()
    return nh3.clean(
        _MD.convert(value), set_tag_attribute_values=attr_values, attributes=_ATTRIBUTES
    )


@register.filter()
@mark_safe
@stringfilter
def markdown(value, arg=None):
    return render_markdown(value, arg)
