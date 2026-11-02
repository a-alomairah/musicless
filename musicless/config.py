import os
from pathlib import Path


class Config:
    def __init__(self):
        self.data = Path(os.getenv('DATA_DIR', '/data'))
        self.models = Path(os.getenv('MODELS_DIR', '/models'))
        self.registry = Path(os.getenv('MODEL_REGISTRY', '/config/models.json'))
        self.max_upload = int(os.getenv('MAX_UPLOAD_MB', '250')) * 1024**2
        self.duration = float(os.getenv('MAX_DURATION_SECONDS', '120'))
        self.queue = int(os.getenv('MAX_QUEUED_JOBS', '3'))
        self.per_user = int(os.getenv('MAX_OUTSTANDING_PER_USER', '1'))
        self.timeout = int(os.getenv('JOB_TIMEOUT_SECONDS', '900'))
        self.retention = int(os.getenv('RETENTION_SECONDS', '3600'))
        self.floor = int(os.getenv('DISK_FLOOR_MB', '2048')) * 1024**2
        self.budget = int(os.getenv('JOB_STORAGE_MB', '1536')) * 1024**2
        self.cpu = os.getenv('ALLOW_CPU', 'false').lower() == 'true'
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / 'jobs').mkdir(exist_ok=True)


class Problem(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)
