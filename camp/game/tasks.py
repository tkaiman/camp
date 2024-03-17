import datetime
import io
from typing import Any
from typing import Callable
from typing import Iterable
from urllib.parse import urljoin

from celery import chord
from celery import shared_task
from celery.result import AsyncResult
from sentry_sdk import add_breadcrumb
from sentry_sdk import capture_exception
from xlsxwriter import Workbook

from camp.accounts.models import User
from camp.character.models import Sheet
from camp.engine.rules.tempest.controllers.character_controller import TempestCharacter
from camp.game.models import Event

from . import models

_WORKBOOK_OPTIONS = {
    "constant_memory": False,
    "remove_timezone": True,
    "default_date_format": "dd mmm yyyy",
}


_XLSLX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_FILENAME_TABLE = str.maketrans(
    {
        "/": "⁄",
        "\\": "⁄",
        "<": "❮",
        ">": "❯",
        "{": "❴",
        "}": "❵",
        ":": "ː",
        "|": "❘",
        "?": "❓",
        "*": "✳",
    }
)


_CHARACTER_COLUMNS: dict[str, Callable[[TempestCharacter], Any]] = {
    "Level": lambda c: c.level.value,
    "Religion": lambda c: c.religion.display_name() if c.religion else "(Unset)",
    "Culture": lambda c: c.culture.display_name() if c.culture else "(Unset)",
    "Primary Breed": lambda c: (
        c.primary_breed.display_name() if c.primary_breed else "(Unset)"
    ),
    "Subbreed": lambda c: c.subbreed.display_name() if c.subbreed else "",
    "Secondary Breed": lambda c: (
        c.secondary_breed.display_name() if c.secondary_breed else "(Unset)"
    ),
    "Primary Class": lambda c: (
        c.primary_class.display_name() if c.primary_class else "(Unset)"
    ),
    "Error": lambda c: None,
}


def generate_report(
    report_type, event_id, requestor, base_url, **kwargs
) -> tuple[models.EventReport, AsyncResult]:
    user = User.objects.filter(username=requestor).first()
    event = Event.objects.get(id=event_id)
    report = models.EventReport.objects.create(
        event=event,
        requestor=user,
        report_type=report_type,
    )

    event = report.event
    pc_regs = event.registrations.filter(
        canceled_date__isnull=True,
        is_npc=False,
        sheet__isnull=False,
    )

    result = chord(
        [sheet_columns.s(r.sheet_id) for r in pc_regs],
        task_id=report.task_id,
    )(
        export_registrations.s(report_id=report.id, base_url=base_url),
    )

    return report, result


@shared_task
def sheet_columns(sheet_id) -> tuple[int, dict[str, Any]]:
    add_breadcrumb(
        category="report",
        message=f"Extracting columns for sheet {sheet_id}",
        level="info",
    )
    sheet = Sheet.objects.get(id=sheet_id)
    char: TempestCharacter
    try:
        char = sheet.controller
        char.reconcile()
    except Exception as exc:
        capture_exception(exc)
        return {"Error": str(exc)}
    add_breadcrumb(
        category="report", message=f"Successfully loaded sheet {sheet_id} ({char})"
    )
    results: dict[str, Any] = {}
    for col, getter in _CHARACTER_COLUMNS.items():
        try:
            results[col] = getter(char)
        except Exception as exc:
            capture_exception(exc)
            results[col] = str(exc)

    return sheet_id, results


@shared_task()
def export_registrations(
    sheet_columns: Iterable[tuple[int, dict[str, Any]]], *, report_id, base_url
) -> int:
    report = models.EventReport.objects.get(pk=report_id)
    event = report.event
    all_regs = list(event.registrations.filter(canceled_date__isnull=True).all())
    stream = io.BytesIO()
    user = report.requestor
    sheet_map = {sheet: columns for (sheet, columns) in sheet_columns}

    with Workbook(stream, _WORKBOOK_OPTIONS) as wb:
        wb.set_properties(
            {
                "title": f"Registrations for {event}",
                "author": __name__,
                "manager": user.username if user else "(unknown)",
                "created": datetime.date.today(),
                "hyperlink_base": base_url,
            }
        )
        header_format = wb.add_format({"bold": True})
        _write_pc_regs(
            wb,
            header_format,
            base_url,
            sheet_map,
            (r for r in all_regs if not r.is_npc),
        )
        _write_npc_regs(wb, header_format, base_url, (r for r in all_regs if r.is_npc))
    report.blob = stream.getvalue()
    report.content_type = _XLSLX_MIMETYPE
    report.filename = _filenameize(f"{event} Registrations.xlsx")
    report.download_ready = True
    report.save()
    return report.pk


def _write_pc_regs(
    wb: Workbook,
    header_format,
    base_url: str,
    sheet_map: dict[int, dict[str, Any]],
    regs: Iterable[models.EventRegistration],
):
    sheet = wb.add_worksheet("PC Registrations")

    # Write header
    sheet.write_row(
        0,
        0,
        [
            "Username",
            "Name",
            "Minor?",
            "Attendance",
            "Lodging",
            "Character",
            "New Player?",
            "New Character?",
            "Paid?",
            "Registered",
            "Link to Registration",
        ]
        + list(_CHARACTER_COLUMNS),
        header_format,
    )
    sheet.freeze_panes(1, 1)
    for i, r in enumerate(regs, start=1):
        user = r.user
        profile = r.profile
        age = profile.age if profile.age < 18 else None
        char = r.character
        new_character = r.character_is_new
        new_player = r.player_is_new
        paid = r.payment_complete
        reg_date = r.registered_date

        j = 0
        sheet.write(i, j, user.username)
        j += 1
        sheet.write(i, j, str(profile))
        j += 1
        sheet.write(i, j, age)
        j += 1
        sheet.write(i, j, r.get_attendance_display())
        j += 1
        sheet.write(i, j, r.get_lodging_display())
        j += 1
        if char:
            char_url = urljoin(base_url, char.get_absolute_url())
            sheet.write_url(i, j, char_url, string=str(char))
            j += 1
        else:
            sheet.write(i, j, "No Character")
            j += 1
        sheet.write(i, j, new_player)
        j += 1
        sheet.write(i, j, new_character)
        j += 1
        sheet.write(i, j, paid)
        j += 1
        sheet.write(i, j, reg_date)
        j += 1
        reg_url = urljoin(base_url, r.get_absolute_url())
        sheet.write_url(
            i,
            j,
            reg_url,
            string=f"Registration for {profile}",
        )
        j += 1
        extra_columns = sheet_map.get(r.sheet_id, {"Error": "No Data"})

        for key in _CHARACTER_COLUMNS.keys():
            value = extra_columns.get(key)
            if value is not None:
                sheet.write(i, j, value)
            j += 1

    sheet.autofit()


def _write_npc_regs(
    wb: Workbook, header_format, base_url: str, regs: Iterable[models.EventRegistration]
):
    sheet = wb.add_worksheet("NPC Registrations")
    # Write header
    sheet.write_row(
        0,
        0,
        [
            "Username",
            "Name",
            "Minor?",
            "Attendance",
            "Lodging",
            "New Player?",
            "New NPC?",
            "Registered",
            "Link to Registration",
        ],
        header_format,
    )
    sheet.freeze_panes(1, 1)
    for i, r in enumerate(regs, start=1):
        user = r.user
        profile = r.profile
        age = profile.age if profile.age < 18 else None
        new_player = r.player_is_new
        new_npc = r.npc_is_new
        reg_date = r.registered_date

        sheet.write(i, 0, user.username)
        sheet.write(i, 1, str(profile))
        sheet.write(i, 2, age)
        sheet.write(i, 3, r.get_attendance_display())
        sheet.write(i, 4, r.get_lodging_display())
        sheet.write(i, 5, new_player)
        sheet.write(i, 6, new_npc)
        sheet.write(i, 7, reg_date)
        sheet.write_url(
            i,
            8,
            urljoin(base_url, r.get_absolute_url()),
            string=f"Registration for {profile}",
        )
    sheet.autofit()


def _filenameize(string: str) -> str:
    return string.translate(_FILENAME_TABLE)
