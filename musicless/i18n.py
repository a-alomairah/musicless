"""Shared Arabic catalogue for server-rendered pages and browser UI."""
import json
import re
from pathlib import Path
from flask import session

ARABIC = json.loads(Path(__file__).with_name('translations.json').read_text(encoding='utf-8'))


def language():
    return session.get('language', 'ar') if session.get('language') in ('ar', 'en') else 'ar'


def translate(message, **values):
    if language() == 'ar':
        message = ARABIC.get(message, message)
    return message.format(**values) if values else message


def detail(message, code=None):
    if language() == 'en' or not message:
        return message
    if message in ARABIC:
        return ARABIC[message]
    match = re.fullmatch(r'Media must be no longer than ([\d.]+) seconds\.', message)
    if match:
        return translate('Media must be no longer than {duration} seconds.', duration=match[1])
    # Preserve model names while translating known preflight diagnostics.
    for source, target in ARABIC.items():
        if message.endswith(': ' + source):
            return message[:-len(source)] + target
    return ARABIC.get(code, 'تعذّر إتمام العملية. يرجى مراجعة المسؤول.')
