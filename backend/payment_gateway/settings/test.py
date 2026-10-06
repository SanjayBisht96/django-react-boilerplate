from .base import *


SECRET_KEY = "test"  # nosec

# Use a dedicated Postgres test database (docker-compose `db-test` service)
DATABASES = {
    "default": config(
        "TEST_DATABASE_URL",
        default="postgres://payment_gateway:password@localhost:5433/payment_gateway_test",
        cast=db_url,
    )
}

STATIC_ROOT = base_dir_join("staticfiles")
STATIC_URL = "/static/"

MEDIA_ROOT = base_dir_join("mediafiles")
MEDIA_URL = "/media/"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Speed up password hashing
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Celery
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# In tests, charge the mock processor in-process (no HTTP server needed)
MOCK_PROCESSOR_URL = ""
