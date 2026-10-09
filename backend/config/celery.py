import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")  # every CELERY_* setting
app.autodiscover_tasks()  # finds tasks.py in each app
