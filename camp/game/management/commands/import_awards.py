import argparse
import csv
import io
import pprint
import sys
from datetime import date
from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction

from camp.engine.rules.tempest.records import AwardCategory
from camp.engine.rules.tempest.records import AwardRecord
from camp.game.models import Campaign
from camp.game.models import Chapter
from camp.game.models.game_models import Award


class DryRun(Exception):
    pass


EMAIL = "Email"
GRANTS = "Grants"
DESCR = "Description"
CATEGORY = "Category"


_DATE_FORMATS = [
    "%Y/%m/%d",
    "%Y-%m-%d",
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
        award_date: date | None = None,
        backstory: bool | None = None,
        category: AwardCategory | None = None,
        dry_run=False,
        **options,
    ):
        committed = False
        try:
            with transaction.atomic():
                campaign = Campaign.objects.get(slug=campaign_id)
                chapter = Chapter.objects.get(slug=chapter_id)

                base_award = AwardRecord(
                    date=award_date or date.today(),
                    backstory_approved=backstory,
                )

                for entry in csv.DictReader(infile):
                    email = entry[EMAIL].strip()

                    if not email:
                        continue

                    this_category = entry.get(CATEGORY) or category
                    description = entry.get(DESCR) or None
                    grants = entry.get(GRANTS) or None

                    self.stdout.write(
                        f"Row: {email}, {this_category}, {grants}, {description}"
                    )

                    award = base_award.model_copy(
                        update={
                            "category": this_category,
                            "description": description,
                            "character_grants": grants.split() if grants else None,
                        }
                    )

                    record_data = award.model_dump(mode="json", exclude_defaults=True)

                    self.stdout.write(
                        f"Creating record for {email}:\n{pprint.pformat(record_data)}"
                    )
                    Award.objects.create(
                        campaign=campaign,
                        email=email,
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
