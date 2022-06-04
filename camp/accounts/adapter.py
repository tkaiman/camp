import allauth.account.adapter
import allauth.socialaccount.adapter
from django.conf import settings

# See https://django-allauth.readthedocs.io/en/latest/advanced.html#creating-and-populating-user-instances
# for details on adapter usage. For now, we're just using them to control whether signup is enabled.


class AccountAdapter(allauth.account.adapter.DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return settings.ACCOUNT_SIGNUP_ENABLED


class SocialAccountAdapter(allauth.socialaccount.adapter.DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, socialaccount):
        return settings.ACCOUNT_SIGNUP_ENABLED
