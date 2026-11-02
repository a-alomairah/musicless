import json
import pytest
from musicless import db
from musicless.worker import run_job
from musicless.config import Problem


def queued(cfg):
    ident=db.reserve(cfg,'admin','big-beta-4','a.wav','.wav')
    db.update(cfg,ident,state='queued')
    db.claim(cfg)
    return ident


def test_worker_revalidates_before_media_or_separator(cfg,monkeypatch):
    ident=queued(cfg)
    (cfg.models/'test.yaml').unlink()
    def forbidden(*args): raise AssertionError('Must fail before invoking media or separation')
    monkeypatch.setattr('musicless.worker.probe',forbidden)
    monkeypatch.setattr('musicless.worker.separate',forbidden)
    assert run_job(cfg,ident)==1
    assert json.loads((cfg.data/'jobs'/ident/'failure.json').read_text())['code']=='MODEL_NOT_AVAILABLE'


@pytest.mark.parametrize('exception,code',[(RuntimeError('CUDA out of memory'),'CUDA_OOM'),(Problem('MEDIA_FAILED','Unreadable media'),'MEDIA_FAILED')])
def test_worker_failure_codes(cfg,monkeypatch,exception,code):
    ident=queued(cfg)
    def failed(*args): raise exception
    monkeypatch.setattr('musicless.worker.probe',failed)
    assert run_job(cfg,ident)==1
    assert json.loads((cfg.data/'jobs'/ident/'failure.json').read_text())['code']==code


def test_success_waits_for_supervisor_verification(cfg,monkeypatch):
    ident=queued(cfg)
    monkeypatch.setattr('musicless.worker.probe',lambda *a:{'duration':2,'video':False})
    monkeypatch.setattr('musicless.worker.extract',lambda *a:None)
    monkeypatch.setattr('musicless.worker.separate',lambda *a:cfg.data/'stem.wav')
    monkeypatch.setattr('musicless.worker.encode',lambda *a:None)
    assert run_job(cfg,ident)==0
    assert db.get(cfg,ident)['state']=='processing'
