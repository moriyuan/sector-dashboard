from datetime import datetime
from .store import TZ


def phase(now: datetime, calendar: list[str]):
    day = now.astimezone(TZ).date().isoformat()
    if not calendar or day > max(calendar):
        return 'calendar_unknown'
    if day not in calendar:
        return 'holiday'
    clock = now.astimezone(TZ).strftime('%H:%M')
    if '09:30' <= clock < '11:30' or '13:00' <= clock < '15:00':
        return 'trading'
    if '12:00' <= clock < '13:00':
        return 'archive_midday'
    if '11:30' <= clock < '13:00':
        return 'lunch'
    if '15:30' <= clock < '16:30':
        return 'archive_1530'
    if clock >= '16:30':
        return 'archive_1630'
    return 'closed'
