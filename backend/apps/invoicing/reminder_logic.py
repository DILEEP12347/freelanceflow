"""Which payment reminder (if any) should go out today? Pure, so it can be tested without a database.

Offsets are days relative to the due date: -3 = three days BEFORE it is due, 1 = one day after, and so on.
"""


def pick_reminder(days_past_due: int, offsets, handled):
    """-> (offset_to_send_or_None, [offsets_to_mark_as_skipped])

    Only ONE reminder goes out per invoice per day, and always the most recent applicable one. Older offsets
    that were never sent (for example the invoice was only sent after they passed) are marked as skipped, so
    the customer never receives a burst of stale reminders later.
    """
    applicable = sorted(o for o in set(offsets) if o <= days_past_due and o not in set(handled))
    if not applicable:
        return None, []
    send = applicable[-1]
    return send, applicable[:-1]
