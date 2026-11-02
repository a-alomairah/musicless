import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from musicless import db
from musicless.config import Problem
from musicless.models import preflight
from musicless.offline import separate, local_separator_class

HEADERS={'X-CSRF-Token':'test-csrf','Content-Type':'application/octet-stream'}


@pytest.mark.parametrize('model',['unknown','https://huggingface.co/a/b/model.ckpt','../model','/models/x.ckpt','C:\\models\\x.ckpt','%2e%2e%2fsecret','--download_model_only','big-beta-4 --help'])
def test_forged_models(client,model):
    response=client.post('/api/jobs',query_string={'filename':'a.wav','model_id':model},data=b'a',headers=HEADERS)
    assert response.status_code==400
    assert response.json['error']=='INVALID_MODEL'


@pytest.mark.parametrize('filename',['../a.wav','/a.wav','C:\\a.wav','%2e%2e.wav','--download_model_only.wav','a\n.wav'])
def test_filenames(client,filename):
    assert client.post('/api/jobs',query_string={'filename':filename},headers=HEADERS,data=b'x').status_code==400


@pytest.mark.parametrize('payload',[{'download_model_only':True},{'model_id':'big-beta-4','options':'--download_model_only'},{'filename':'x.wav','model_url':'https://example.com/x'}])
def test_unexpected_json_and_forms(client,payload):
    assert client.post('/api/jobs',json=payload,headers={'X-CSRF-Token':'test-csrf'}).status_code==400
    assert client.post('/api/jobs',data=payload,headers={'X-CSRF-Token':'test-csrf'}).status_code==400
    assert client.post('/api/jobs',query_string=payload,headers=HEADERS,data=b'x').status_code==400


@pytest.mark.parametrize('failure',['checkpoint','yaml','truncated','corrupt','unapproved_hash'])
def test_model_missing_or_corrupt_before_loader(cfg,client,worker,monkeypatch,failure):
    if failure=='checkpoint': (cfg.models/'test.ckpt').unlink()
    if failure=='yaml': (cfg.models/'test.yaml').unlink()
    if failure=='truncated': (cfg.models/'test.ckpt').write_bytes(b'PK')
    if failure=='corrupt': (cfg.models/'test.ckpt').write_bytes(b'not a valid archive'*30)
    if failure=='unapproved_hash':
        entry=json.loads(cfg.registry.read_text());entry['models'][0]['sha256']=None;cfg.registry.write_text(json.dumps(entry))
    before={p.name:p.read_bytes() for p in cfg.models.iterdir()}
    def forbidden(*args,**kwargs): raise AssertionError('Network must not be attempted')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    with pytest.raises(Problem,match='Test model'):
        separate(cfg,'big-beta-4',cfg.data/'none.wav',cfg.data)
    response=client.post('/api/jobs?filename=x.wav',data=b'x',headers=HEADERS)
    assert response.status_code==409
    assert response.json['error']=='MODEL_NOT_AVAILABLE'
    assert before=={p.name:p.read_bytes() for p in cfg.models.iterdir()}


def test_local_adapter_never_uses_catalog(cfg):
    class FakeBase:
        def load(self):
            return self.download_model_files('test.ckpt')
    adapter=local_separator_class(FakeBase,cfg,'big-beta-4')()
    assert adapter.load()[1]=='MDXC'
    for method in ('download_file_if_not_exists','list_supported_model_files','load_model_data_using_hash'):
        with pytest.raises(RuntimeError): getattr(adapter,method)()
    with pytest.raises(Problem): adapter.download_model_files('other.ckpt')


def test_auth_csrf_permissions(app,client):
    anon=app.test_client()
    assert anon.get('/api/jobs').json==[]
    assert anon.get('/api/status').status_code==200
    assert anon.get('/api/jobs/x/download').status_code==404
    assert client.post('/api/settings',json={}).status_code==403
    with client.session_transaction() as session: session['role']='user'
    assert client.post('/api/settings',json={'default_model':'big-beta-4','allow_choice':False},headers={'X-CSRF-Token':'test-csrf'}).status_code==403
    assert client.get('/settings').status_code==403


def test_download_owner_and_expiry(cfg,client):
    ident=db.reserve(cfg,'other','big-beta-4','a.wav','.wav')
    db.update(cfg,ident,state='complete',expires=time.time()+10)
    assert client.get(f'/api/jobs/{ident}/download').status_code==404
    with db.connect(cfg) as conn: conn.execute("UPDATE jobs SET owner='admin',expires=? WHERE id=?",(time.time()-1,ident))
    assert client.get(f'/api/jobs/{ident}/download').status_code==404


def test_streaming_size_limit(cfg,client,worker):
    cfg.max_upload=5
    response=client.post('/api/jobs?filename=a.wav',headers=HEADERS,data=b'123456')
    assert response.status_code==413
    assert not list((cfg.data/'jobs').rglob('input*'))


def test_simultaneous_uploads(app,cfg,worker):
    def upload(n):
        with app.test_client() as c:
            with c.session_transaction() as session: session.update(user=f'user{n}',role='user',csrf='test-csrf')
            return c.post('/api/jobs?filename=a.wav',headers=HEADERS,data=b'x').status_code
    with ThreadPoolExecutor(max_workers=8) as executor: results=list(executor.map(upload,range(8)))
    assert results.count(202)==3
    assert results.count(429)==5
    with ThreadPoolExecutor(max_workers=8) as executor: claims=list(executor.map(lambda _:db.claim(cfg),range(8)))
    assert sum(row is not None for row in claims)==1


def test_same_user_limit(cfg,client,worker):
    assert client.post('/api/jobs?filename=a.wav',headers=HEADERS,data=b'x').status_code==202
    assert client.post('/api/jobs?filename=b.wav',headers=HEADERS,data=b'x').status_code==429


def test_settings_rejects_extra_properties(client):
    r=client.post('/api/settings',json={'default_model':'big-beta-4','allow_choice':False,'download_model_only':True},headers={'X-CSRF-Token':'test-csrf'})
    assert r.status_code==400


def test_login_logout(app):
    c=app.test_client()
    assert c.get('/').status_code==200
    c.get('/login')
    with c.session_transaction() as session: token=session['csrf']
    assert c.post('/login',data={'csrf':token,'username':'admin','password':'test-password-long'}).status_code==302
    assert c.get('/').status_code==200
    with c.session_transaction() as session: token=session['csrf']
    assert c.post('/logout',data={'csrf':token}).status_code==302
    assert c.get('/api/jobs').status_code==200
    assert c.get('/settings').status_code==403


def test_public_upload_and_private_download(app, cfg, worker):
    visitor = app.test_client()
    other = app.test_client()
    page = visitor.get('/')
    assert page.status_code == 200
    assert 'دخول المسؤول' in page.get_data(as_text=True)
    assert b'href="/settings"' not in page.data
    with visitor.session_transaction() as session:
        token = session['csrf']
    headers = {'X-CSRF-Token': token, 'Content-Type': 'application/octet-stream'}
    assert visitor.post('/api/jobs?filename=a.wav', data=b'a').status_code == 403
    response = visitor.post('/api/jobs?filename=a.wav', data=b'a', headers=headers)
    assert response.status_code == 202
    ident = response.json['id']
    assert visitor.get('/api/jobs').json[0]['id'] == ident
    assert other.get('/api/jobs').json == []
    with other.session_transaction() as session:
        other_token = session['csrf']
    assert other.post(f'/api/jobs/{ident}/cancel', headers={'X-CSRF-Token': other_token}).status_code == 404
    assert visitor.post('/api/jobs?filename=b.wav', data=b'b', headers=headers).status_code == 429
    (cfg.data / 'jobs' / ident / 'output.wav').write_bytes(b'processed audio')
    db.update(cfg, ident, state='complete', expires=time.time()+60)
    assert other.get(f'/api/jobs/{ident}/download').status_code == 404
    download = visitor.get(f'/api/jobs/{ident}/download')
    assert download.status_code == 200
    assert download.data == b'processed audio'
    assert 'a_musicless.wav' in download.headers['Content-Disposition']
    assert visitor.get('/settings').status_code == 403
    assert visitor.post('/api/settings', json={'default_model': 'big-beta-4', 'allow_choice': False}, headers={'X-CSRF-Token': token}).status_code == 403
    response = visitor.post('/api/jobs?filename=b.wav', data=b'b', headers=headers)
    assert response.status_code == 202
    assert visitor.post(f"/api/jobs/{response.json['id']}/cancel", headers=headers).status_code == 200
    assert db.get(cfg, response.json['id'])['cancel'] == 1


def test_admin_login_logout_preserves_visitor_jobs(app, cfg):
    visitor = app.test_client()
    visitor.get('/')
    with visitor.session_transaction() as session:
        owner, token = session['visitor'], session['csrf']
    ident = db.reserve(cfg, owner, 'big-beta-4', 'a.wav', '.wav')
    assert visitor.post('/login', data={'csrf': token, 'username': 'admin', 'password': 'test-password-long'}).status_code == 302
    assert visitor.get('/settings').status_code == 200
    assert visitor.get('/api/jobs').json == []
    with visitor.session_transaction() as session:
        token = session['csrf']
    assert visitor.post('/logout', data={'csrf': token}).headers['Location'] == '/'
    assert visitor.get('/settings').status_code == 403
    assert visitor.get('/api/jobs').json[0]['id'] == ident
