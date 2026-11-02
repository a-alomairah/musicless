import hashlib
import json
import time
import zipfile
import pytest
from werkzeug.security import generate_password_hash
from musicless.config import Config
from musicless import db
from musicless.web import create_app


@pytest.fixture
def cfg(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setenv('MODELS_DIR',str(tmp_path/'models'))
    monkeypatch.setenv('MODEL_REGISTRY',str(tmp_path/'models.json'))
    monkeypatch.setenv('DISK_FLOOR_MB','0')
    monkeypatch.setenv('JOB_STORAGE_MB','16')
    config=Config()
    config.models.mkdir()
    checkpoint=config.models/'test.ckpt'
    with zipfile.ZipFile(checkpoint,'w') as out:
        out.writestr('archive/data.pkl',b'test-only-inert-placeholder')
    yaml=config.models/'test.yaml'
    yaml.write_text('audio: {sample_rate: 44100}\nmodel: {}\ntraining: {}\ninference: {}\n')
    config.registry.write_text(json.dumps({'models':[{'id':'big-beta-4','name':'Test model','checkpoint':checkpoint.name,'config':yaml.name,'min_bytes':1,'sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),'config_sha256':hashlib.sha256(yaml.read_bytes()).hexdigest()}]}))
    db.init(config)
    return config


@pytest.fixture
def app(cfg,monkeypatch):
    monkeypatch.setenv('SESSION_SECRET','s'*64)
    monkeypatch.setenv('ADMIN_PASSWORD_HASH',generate_password_hash('test-password-long'))
    monkeypatch.setenv('COOKIE_SECURE','false')
    app=create_app(cfg)
    app.config['TESTING']=True
    return app


@pytest.fixture
def client(app):
    client=app.test_client()
    with client.session_transaction() as session:
        session.update(user='admin',role='admin',csrf='test-csrf')
    return client


@pytest.fixture
def worker(cfg):
    """Exercise the actual filesystem preflight via a lightweight worker reporter."""
    import threading
    from musicless.worker import refresh
    stop=threading.Event()
    def loop():
        while not stop.is_set():
            db.put(cfg,'worker',{'heartbeat':time.time(),'ready':True,'cuda':True})
            refresh(cfg)
            stop.wait(.02)
    thread=threading.Thread(target=loop)
    thread.start()
    time.sleep(.05)
    yield
    stop.set()
    thread.join()
