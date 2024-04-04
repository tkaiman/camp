from allauth.account import forms as allauth_forms
from django import forms
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.fields import ReCaptchaV2Checkbox

from camp.game.fields import DateField

from . import models


class MembershipForm(forms.ModelForm):
    birthdate = DateField(help_text=models.Membership.birthdate.field.help_text)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Once you've set your birthdate, you can't change it without intervention.
        if self.instance.birthdate is not None:
            bday: forms.Field = self.fields["birthdate"]
            bday.disabled = True

    class Meta:
        model = models.Membership
        exclude = ["joined", "game", "user"]


RECAPTCHA_ATTRS = {"data-theme": "dark"}


class SignupForm(allauth_forms.SignupForm):
    captcha = ReCaptchaField(
        widget=ReCaptchaV2Checkbox(
            attrs=RECAPTCHA_ATTRS,
        )
    )

    field_order = ["username", "email", "password1", "captcha"]


class ResetPasswordForm(allauth_forms.ResetPasswordForm):
    captcha = ReCaptchaField(
        widget=ReCaptchaV2Checkbox(
            attrs=RECAPTCHA_ATTRS,
        )
    )


class AddEmailForm(allauth_forms.AddEmailForm):
    captcha = ReCaptchaField(
        widget=ReCaptchaV2Checkbox(
            attrs=RECAPTCHA_ATTRS,
        )
    )
