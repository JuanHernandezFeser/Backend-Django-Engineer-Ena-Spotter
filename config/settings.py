from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="local-development-only-not-a-secret")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "testserver"]
)

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "fuel",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "fuel.middleware.RequestTimingMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": env("DB_NAME", default=str(BASE_DIR / "db.sqlite3")),
    }
}

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "EXCEPTION_HANDLER": "fuel.exceptions.custom_exception_handler",
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuel-route-planner",
    }
}

ROUTING_URL = env(
    "ROUTING_URL", default="https://router.project-osrm.org/route/v1/driving"
)
ROUTING_TIMEOUT_SECONDS = env.float("ROUTING_TIMEOUT_SECONDS", default=8.0)
ROUTE_CACHE_SECONDS = env.int("ROUTE_CACHE_SECONDS", default=86400)
ROUTE_SIMPLIFY_TOLERANCE = env.float("ROUTE_SIMPLIFY_TOLERANCE", default=0.0002)

TEXT_GEOCODER = env("TEXT_GEOCODER", default="open-meteo").lower()
OPEN_METEO_URL = env(
    "OPEN_METEO_URL", default="https://geocoding-api.open-meteo.com/v1/search"
)
OPEN_METEO_TIMEOUT_SECONDS = env.float("OPEN_METEO_TIMEOUT_SECONDS", default=10.0)
OPEN_METEO_MAX_CONSECUTIVE_FAILURES = env.int(
    "OPEN_METEO_MAX_CONSECUTIVE_FAILURES", default=20
)

PHOTON_URL = env("PHOTON_URL", default="https://photon.komoot.io/api")
PHOTON_USER_AGENT = env(
    "PHOTON_USER_AGENT", default="fuel-route-planner/1.0 (local development)"
)
GEOCODE_CACHE_SECONDS = env.int("GEOCODE_CACHE_SECONDS", default=604800)
GEOCODE_CACHE_FILE = env(
    "GEOCODE_CACHE_FILE", default=str(BASE_DIR / "data" / "geocode_cache.json")
)
GEOCODE_DELAY_SECONDS = env.float("GEOCODE_DELAY_SECONDS", default=1.1)
PHOTON_CONNECT_TIMEOUT_SECONDS = env.float("PHOTON_CONNECT_TIMEOUT_SECONDS", default=5.0)
PHOTON_TIMEOUT_SECONDS = env.float("PHOTON_TIMEOUT_SECONDS", default=20.0)
PHOTON_RETRIES = env.int("PHOTON_RETRIES", default=2)
PHOTON_MAX_CONSECUTIVE_FAILURES = env.int("PHOTON_MAX_CONSECUTIVE_FAILURES", default=10)

STATION_SEARCH_RADIUS_MILES = env.float("STATION_SEARCH_RADIUS_MILES", default=5.0)
TANK_RANGE_MILES = env.float("TANK_RANGE_MILES", default=500.0)
MPG = env.float("MPG", default=10.0)

CSV_FILE = env("CSV_FILE", default=str(BASE_DIR / "fuel-prices-for-be-assessment.csv"))
