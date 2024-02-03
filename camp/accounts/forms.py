from django import forms

from camp.game.fields import DateField

from . import models


class MembershipForm(forms.ModelForm):
    birthdate = DateField(help_text=models.Membership.birthdate.field.help_text)

    class Meta:
        model = models.Membership
        exclude = ["joined", "game", "user"]
