import time
import pytest
from musicless import db
from musicless.config import Problem


def test_cleanup_preserves_running(cfg):
    active=db.reserve(cfg,'a','big-beta-4','a.wav','.wav')
    db.update(cfg,active,state='queued')
    db.claim(cfg)
    running=cfg.data/'jobs'/active/'audio.wav';running.write_bytes(b'keep')
    expired=db.reserve(cfg,'b','big-beta-4','a.wav','.wav')
    db.update(cfg,expired,state='complete',expires=time.time()-1)
    failed=db.reserve(cfg,'c','big-beta-4','a.wav','.wav')
    db.update(cfg,failed,state='failed')
    db.cleanup(cfg)
    assert running.read_bytes()==b'keep'
    assert not (cfg.data/'jobs'/expired).exists()
    assert not (cfg.data/'jobs'/failed).exists()
    assert db.get(cfg,expired)['state']=='expired'


def test_persistent_queue_and_stale_upload(cfg):
    ident=db.reserve(cfg,'a','big-beta-4','a.wav','.wav')
    db.init(cfg)
    assert db.get(cfg,ident)['state']=='uploading'
    with db.connect(cfg) as conn: conn.execute('UPDATE jobs SET updated=? WHERE id=?',(time.time()-1300,ident))
    db.cleanup(cfg)
    assert db.get(cfg,ident)['state']=='failed'


def test_disk_floor(cfg):
    cfg.floor=10**18
    with pytest.raises(Problem,match='space'): db.reserve(cfg,'a','big-beta-4','a.wav','.wav')
