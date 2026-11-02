import time
from musicless import db


def switch(client, language, destination='/'):
    with client.session_transaction() as session:
        token = session['csrf']
    return client.post('/language', data={'csrf': token, 'language': language, 'next': destination})


def test_default_arabic_and_public_pages(app):
    client = app.test_client()
    for path in ('/', '/login', '/privacy'):
        response = client.get(path)
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert '<html lang="ar" dir="rtl">' in html
        assert 'ENG' in html and 'ARA' in html
        assert 'وليس في متصفحك' in html
    assert 'دخول المسؤول' in client.get('/').get_data(as_text=True)


def test_switch_persists_through_auth(app):
    client = app.test_client()
    client.get('/')
    assert switch(client, 'en', '/privacy').headers['Location'] == '/privacy'
    for path in ('/', '/login', '/privacy'):
        assert '<html lang="en" dir="ltr">' in client.get(path).get_data(as_text=True)
    with client.session_transaction() as session:
        token = session['csrf']
    client.post('/login', data={'csrf': token, 'username': 'admin', 'password': 'test-password-long'})
    assert 'Worker diagnostics' in client.get('/settings').get_data(as_text=True)
    with client.session_transaction() as session:
        token = session['csrf']
    client.post('/logout', data={'csrf': token})
    assert '<html lang="en" dir="ltr">' in client.get('/').get_data(as_text=True)
    switch(client, 'ar')
    assert '<html lang="ar" dir="rtl">' in client.get('/').get_data(as_text=True)


def test_switch_security(client):
    assert client.post('/language', data={'language': 'en'}).status_code == 403
    assert switch(client, 'fr').status_code == 400
    assert switch(client, 'en', 'https://example.com').headers['Location'] == '/'


def test_privacy_uses_configured_values(cfg, client):
    cfg.retention = 7321
    cfg.timeout = 456
    for lang in ('ar', 'en'):
        switch(client, lang, '/privacy')
        html = client.get('/privacy').get_data(as_text=True)
        assert '7321' in html and '456' in html
    assert '24 hours' in html and '30 seconds' in html
    assert 'not a guarantee of immediate physical deletion' in html


def test_job_details_and_api_errors_are_localized(cfg, client):
    ident = db.reserve(cfg, 'admin', 'big-beta-4', 'صوت.wav', '.wav')
    db.update(cfg, ident, state='processing', stage='Separating vocals')
    row = client.get('/api/jobs').json[0]
    assert row['state'] == 'processing'
    assert row['stage'] == 'جارٍ فصل الصوت عن الموسيقى'
    assert row['filename'] == 'صوت.wav'
    response = client.post('/api/settings', json={})
    assert response.json['error'] == 'CSRF'
    assert 'رمز الحماية' in response.json['message']
    db.put(cfg, 'availability', {'checked_at': time.time(), 'models': {'big-beta-4': {'available': False, 'reason': 'Test model: File integrity check failed'}}})
    assert 'فشل التحقق' in client.get('/api/status').json['availability']['models']['big-beta-4']['reason']
    switch(client, 'en')
    assert client.get('/api/jobs').json[0]['stage'] == 'Separating vocals'
    # Localization must never rewrite persisted job or diagnostic data.
    assert db.get(cfg, ident)['stage'] == 'Separating vocals'
    assert 'File integrity' in db.setting(cfg, 'availability')['models']['big-beta-4']['reason']
