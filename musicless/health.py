import sys
import time
from .config import Config
from . import db

cfg=Config()
worker=db.setting(cfg,'worker',{})
sys.exit(0 if time.time()-worker.get('heartbeat',0)<90 and worker.get('ready') else 1)
