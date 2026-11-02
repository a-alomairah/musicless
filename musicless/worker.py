import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from . import db
from .config import Config, Problem
from .models import registry, preflight
from .offline import deny_network, separate
from .media import probe, extract, encode


def run_job(cfg, ident):
    row = db.get(cfg,ident)
    folder = cfg.data/'jobs'/ident
    try:
        preflight(cfg,row['model'])
        source=folder/('input'+row['ext'])
        db.update(cfg,ident,stage='Validating media')
        meta=probe(source,row['ext'],cfg)
        db.update(cfg,ident,stage='Extracting full audio')
        wav=folder/'audio.wav'
        extract(source,wav,meta,cfg)
        db.update(cfg,ident,stage='Separating vocals')
        stems=folder/'stems'
        stems.mkdir()
        vocals=separate(cfg,row['model'],wav,stems)
        db.update(cfg,ident,stage='Encoding and verifying output')
        encode(source,vocals,folder/('output'+row['ext']),row['ext'],meta,cfg)
        # Supervisor marks complete only after the child exits successfully.
        return 0
    except Exception as exc:
        code=exc.code if isinstance(exc,Problem) else ('CUDA_OOM' if 'out of memory' in str(exc).lower() else 'PROCESSING_FAILED')
        logging.exception('Job %s failed (%s)', ident, code)
        (folder/'failure.json').write_text(json.dumps({'code':code,'message':exc.message if isinstance(exc,Problem) else ''}))
        return 1


def diagnostic(cfg):
    try:
        import torch
        cuda=torch.cuda.is_available()
        result={'cuda':cuda,'gpu':torch.cuda.get_device_name(0) if cuda else None,'free_vram_mb':torch.cuda.mem_get_info()[0]//1024**2 if cuda else 0,'torch':torch.__version__,'cuda_build':torch.version.cuda,'ready':cuda or cfg.cpu,'cpu_enabled':cfg.cpu}
    except Exception:
        result={'cuda':False,'ready':False,'gpu':None,'free_vram_mb':0}
    return result


def refresh(cfg):
    checked=time.time()
    results={}
    for ident in registry(cfg):
        try:
            preflight(cfg,ident)
            results[ident]={'available':True,'reason':'Files verified; GPU inference is validated by an actual job.'}
        except Problem as exc:
            results[ident]={'available':False,'reason':exc.message}
    db.put(cfg,'availability',{'checked_at':checked,'models':results})


def stop_process(proc):
    if proc.poll() is None:
        os.killpg(proc.pid,signal.SIGKILL)
    proc.wait()


def main():
    import fcntl
    cfg=Config()
    db.init(cfg)
    lock=(cfg.data/'worker.lock').open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    deny_network()
    with db.connect(cfg) as conn:
        conn.execute("UPDATE jobs SET state='failed',error='WORKER_RESTARTED',updated=? WHERE state='processing'",(time.time(),))
    stopping=False
    def shutdown(*args):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,shutdown)
    signal.signal(signal.SIGINT,shutdown)
    diag=diagnostic(cfg)
    proc=None
    row=None
    last_refresh=0
    last_cleanup=0
    while not stopping:
        db.put(cfg,'worker',{**diag,'heartbeat':time.time()})
        if db.setting(cfg,'preflight_request',0)>last_refresh or time.time()-last_refresh>60:
            refresh(cfg)
            last_refresh=db.setting(cfg,'availability')['checked_at']
        if proc:
            current=db.get(cfg,row['id'])
            folder=cfg.data/'jobs'/row['id']
            used=sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
            failure=None
            if current['cancel']:
                failure='CANCELLED'
            elif time.monotonic()-started>cfg.timeout:
                failure='PROCESSING_TIMEOUT'
            elif used>cfg.budget or shutil.disk_usage(cfg.data).free<cfg.floor:
                failure='DISK_LIMIT'
            if failure:
                stop_process(proc)
            if proc.poll() is not None:
                if failure or proc.returncode:
                    details=folder/'failure.json'
                    detail=json.loads(details.read_text()) if details.exists() else {}
                    failure=failure or detail.get('code','WORKER_FAILED')
                    messages={'CUDA_OOM':'The model ran out of GPU memory. No model or quality change was made.','PROCESSING_TIMEOUT':'Processing exceeded the configured time limit.','DISK_LIMIT':'Processing stopped at the storage limit.','CANCELLED':'Cancelled.','PROCESSING_FAILED':'Separation failed. Ask the administrator to check worker logs.','WORKER_FAILED':'The processing worker exited unexpectedly.'}
                    message=detail.get('message') or messages.get(failure,failure)
                    db.update(cfg,row['id'],state='cancelled' if failure=='CANCELLED' else 'failed',error=failure,stage=message)
                else:
                    for path in folder.iterdir():
                        if path.name!='output'+row['ext']:
                            shutil.rmtree(path) if path.is_dir() else path.unlink()
                    db.update(cfg,row['id'],state='complete',stage='Ready to download',expires=time.time()+cfg.retention)
                proc=None
                db.cleanup(cfg)
        if not proc and diag['ready']:
            row=db.claim(cfg)
            if row:
                if row['cancel']:
                    db.update(cfg,row['id'],state='cancelled',error='CANCELLED')
                else:
                    started=time.monotonic()
                    proc=subprocess.Popen([sys.executable,'-m','musicless.worker','--job',row['id']],start_new_session=True)
        if time.time()-last_cleanup>30:
            db.cleanup(cfg)
            last_cleanup=time.time()
        time.sleep(.5)
    if proc:
        stop_process(proc)
        db.update(cfg,row['id'],state='failed',error='WORKER_STOPPED')
    db.cleanup(cfg)


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--job':
        deny_network()
        sys.exit(run_job(Config(),sys.argv[2]))
    main()
