import argparse
import csv
import io
import pprint
import sys
from datetime import date
from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction

from camp.accounts.models import User
from camp.engine.rules.tempest.records import AwardCategory
from camp.engine.rules.tempest.records import AwardRecord
from camp.game import models
from camp.game.models import Campaign
from camp.game.models import Chapter
from camp.game.models.game_models import Award


class DryRun(Exception):
    pass


EMAIL = "Email"
USERNAME = "Username"
CHAR = "Character"
CATEGORY = "Category"
DATE = "Date"
PFLAGS = "Player Flags"
CFLAGS = "Character Flags"
GRANTS = "Character Grants"
EVENT_XP = "Event XP"
EVENT_CP = "Event CP"
BONUS_CP = "Bonus CP"
DESCR = "Description"


_DATE_FORMATS = [
    "%Y/%m/%d",
    "%Y-%m-%d",
    "%m/%d/%Y",
]


def DateType(string):
    if not string:
        return None
    for format in _DATE_FORMATS:
        try:
            return datetime.strptime(string, format).date()
        except ValueError:
            pass
    raise ValueError(f"Unable to parse date {string!r}")


class Command(BaseCommand):
    help = "Import award data from a CSV file."

    def add_arguments(self, parser):
        parser.add_argument("campaign_id", type=str)
        parser.add_argument("chapter_id", type=str)
        parser.add_argument("infile", type=argparse.FileType("r"))
        parser.add_argument("--category", type=AwardCategory)
        parser.add_argument("--backstory", action=argparse.BooleanOptionalAction)
        parser.add_argument("-d", "--award_date", type=DateType)
        parser.add_argument("-n", "--dry-run", action="store_true", default=False)

    def handle(
        self,
        campaign_id: str,
        chapter_id: str,
        infile: io.TextIOBase,
        dry_run=False,
        **options,
    ):
        committed = False
        try:
            with transaction.atomic():
                campaign = Campaign.objects.get(slug=campaign_id)
                chapter = Chapter.objects.get(slug=chapter_id)
                today = date.today()

                for entry in csv.DictReader(infile):
                    email = entry.get(EMAIL, "").strip()
                    username = entry.get(USERNAME, "").strip()
                    if award_date_str := entry.get(DATE):
                        award_date = DateType(award_date_str)
                    else:
                        award_date = today

                    if event_xp_str := entry.get(EVENT_XP):
                        event_xp = int(event_xp_str)
                    else:
                        event_xp = None

                    if event_cp_str := entry.get(EVENT_CP):
                        event_cp = int(event_cp_str)
                    else:
                        event_cp = None

                    if bonus_cp_str := entry.get(BONUS_CP):
                        bonus_cp = int(bonus_cp_str)
                    else:
                        bonus_cp = None

                    if grants_str := entry.get(GRANTS):
                        grants = grants_str.split()
                    else:
                        grants = None

                    if pflags_str := entry.get(PFLAGS):
                        pflags = pflags_str.split()
                    else:
                        pflags = None

                    if cflags_str := entry.get(CFLAGS):
                        cflags = cflags_str.split()
                    else:
                        cflags = None

                    if not (email or username):
                        continue

                    category = entry.get(CATEGORY)
                    description = entry.get(DESCR) or None

                    match category:
                        case AwardCategory.EVENT:
                            event = models.Event.objects.filter(
                                event_end_date=award_date,
                                chapter=chapter,
                            ).get()
                            source_id = event.id
                            if event_xp:
                                event_xp = min(event_xp, 2 * event.logistics_periods)
                        case _:
                            source_id = None

                    award = AwardRecord(
                        source_id=source_id,
                        category=category,
                        date=award_date,
                        description=description,
                        character_grants=grants,
                        player_flags=pflags,
                        character_flags=cflags,
                        event_xp=event_xp,
                        event_cp=event_cp,
                        bonus_cp=bonus_cp,
                    )

                    record_data = award.model_dump(mode="json", exclude_defaults=True)

                    self.stdout.write(
                        f"Creating record for {email or username}:\n{pprint.pformat(record_data)}"
                    )
                    if username:
                        user = User.objects.filter(username=username).first()
                    else:
                        user = None

                    Award.objects.create(
                        campaign=campaign,
                        email=email,
                        user=user,
                        award_data=record_data,
                        chapter=chapter,
                    )

                if dry_run:
                    raise DryRun()
                committed = True
        except DryRun:
            self.stdout.write(self.style.NOTICE("Dry run, rolled back"))

        if dry_run and committed:
            self.stdout.write(
                self.style.ERROR("Dry run requested but committed anyway.")
            )
            sys.exit(1)
        if committed and not dry_run:
            self.stdout.write(self.style.SUCCESS("Completed successfully."))
