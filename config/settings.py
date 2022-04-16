from pathlib import Path
import environs

env = environs.Env()

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = env.str("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = [env.str("DJANGO_HOSTNAME", ".herokuapp.com")]
if DEBUG:
    ALLOWED_HOSTS += ["localhost", "0.0.0.0", "127.0.0.1"]

# In production we may use a different URL for the admin interface.
ADMIN_URL = env.str('ADMIN_URL', 'admin/')

SITE_ID = 1
LANGUAGE_CODE = 'en-us'
TIME_ZONE = env.str('TIME_ZONE', default='UTC')
USE_TZ = True

# Security settings
SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', 60)
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'whitenoise.runserver_nostatic',
    'django.contrib.staticfiles',
    'django.contrib.sites',

    # Third-party
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    # Social auth providers. See here for the full available list:
    # https://django-allauth.readthedocs.io/en/latest/installation.html
    'allauth.socialaccount.providers.discord',
    'allauth.socialaccount.providers.google',

    'crispy_forms',
    'crispy_bootstrap5',
    'debug_toolbar',

    # Local
    'pages',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'debug_toolbar.middleware.DebugToolbarMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'APP_DIRS': True,
        'DIRS': [ BASE_DIR / 'templates' ],
        'OPTIONS': {
            'debug': DEBUG,
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

DATABASES = {
    'default': env.dj_db_url('DATABASE_URL', default=f'sqlite:///{BASE_DIR / "db.sqlite3"}', ssl_require=not DEBUG)
}

AUTHENTICATION_BACKENDS = (
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
)
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

STATIC_ROOT = str(BASE_DIR / 'staticfiles')
STATIC_URL = '/static/'
STATICFILES_DIRS = [str(BASE_DIR / 'static')]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'


# THIRD PARTY APP CONFIGS

# crispy_forms
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# allauth

# Login using either username or email address
ACCOUNT_AUTHENTICATION_METHOD = 'username_email'
# Requires an email be provided at signup if true
ACCOUNT_EMAIL_REQUIRED = False  # Let's see if all our social auths provide this
# Require verification? 'mandatory' = no login until confirmed... maybe later.
ACCOUNT_EMAIL_VERIFICATION = 'optional'
# Usernames are stored lowercase
ACCOUNT_PRESERVE_USERNAME_CASING = False
# Only ask for the password once. Use password reset if you fail.
ACCOUNT_SIGNUP_PASSWORD_ENTER_TWICE = False
# TODO: Rate limiting requires a cache other than DummyCache. Ensure we have that set up.
ACCOUNT_RATE_LIMITS = {
    # Change password view (for users already logged in)
    "change_password": "5/m",
    # Email management (e.g. add, remove, change primary)
    "manage_email": "10/m",
    # Request a password reset, global rate limit per IP
    "reset_password": "20/m",
    # Rate limit measured per individual email address
    "reset_password_email": "5/m",
    # Password reset (the view the password reset email links to).
    "reset_password_from_key": "20/m",
    # Signups.
    "signup": "20/m",
    # NOTE: Login is already protected via `ACCOUNT_LOGIN_ATTEMPTS_LIMIT`
}

# Heroku integration
import django_heroku
django_heroku.settings(locals())