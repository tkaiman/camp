from allauth.account import forms as allauth_forms
from django import forms
from django.conf import settings
from django_recaptcha import widgets as rcwidgets
from django_recaptcha.fields import ReCaptchaField

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


def _recaptcha_field(action=None) -> ReCaptchaField:
    match settings.RECAPTCHA_VERSION:
        case "v2i":
            widget = rcwidgets.ReCaptchaV2Invisible(attrs=RECAPTCHA_ATTRS)
        case "v2c":
            widget = rcwidgets.ReCaptchaV2Checkbox(attrs=RECAPTCHA_ATTRS)
        case "v3":
            widget = rcwidgets.ReCaptchaV3(attrs=RECAPTCHA_ATTRS, action=action)
        case _:
            raise ValueError(
                f"Invalid recaptcha version setting {settings.RECAPTCHA_VERSION}"
            )

    return ReCaptchaField(
        widget=widget,
    )


class SignupForm(allauth_forms.SignupForm):
    captcha = _recaptcha_field(action="signup")

    field_order = ["username", "email", "password1", "captcha"]


class ResetPasswordForm(allauth_forms.ResetPasswordForm):
    captcha = _recaptcha_field(action="reset_password")


class AddEmailForm(allauth_forms.AddEmailForm):
    captcha = _recaptcha_field(action="add_email")
