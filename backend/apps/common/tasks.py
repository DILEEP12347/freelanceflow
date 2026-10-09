"""One safe way to queue background work."""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def dispatch(task, *args, **kwargs):
    """Queue a Celery task without ever breaking the request that asked for it.

    - Normal operation: task.delay(...) puts it on the Redis queue for the worker.
    - CELERY_TASK_ALWAYS_EAGER (set automatically while running tests, or with CELERY_TASK_ALWAYS_EAGER=1):
      runs inline, and errors are raised so tests see them.
    - If the queue is unreachable we log it and carry on: the invoice was already saved, and the email can be
      re-sent from the API.
    """
    try:
        if settings.CELERY_TASK_ALWAYS_EAGER:
            return task.apply(args=args, kwargs=kwargs, throw=True)
        return task.delay(*args, **kwargs)
    except Exception:
        logger.exception("Could not queue task %s", getattr(task, "name", task))
        if settings.CELERY_TASK_ALWAYS_EAGER:
            raise
        return None
