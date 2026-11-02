"""Administrator-owned registry. No catalog imports, remote discovery or downloads."""
import hashlib
import json
import re
import stat
import zipfile
import yaml
from .config import Problem


class ModelConfigLoader(yaml.SafeLoader):
    """Safe YAML plus the tuple sequences used by RoFormer configurations."""


def _construct_tuple(loader, node):
    return tuple(loader.construct_sequence(node, deep=True))


ModelConfigLoader.add_constructor('tag:yaml.org,2002:python/tuple', _construct_tuple)


def registry(cfg):
    entries = json.loads(cfg.registry.read_text(encoding='utf-8'))['models']
    result = {}
    for entry in entries:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', entry['id']) or entry['id'] in result:
            raise ValueError('Invalid or duplicate registry ID')
        for key, suffix in [('checkpoint', '.ckpt'), ('config', '.yaml')]:
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', entry[key]) or not entry[key].endswith(suffix):
                raise ValueError('Registry filenames must be simple local filenames')
        result[entry['id']] = entry
    return result


def resolve(cfg, model_id):
    if not isinstance(model_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', model_id):
        raise Problem('INVALID_MODEL', 'Choose an approved model ID.')
    entry = registry(cfg).get(model_id)
    if not entry:
        raise Problem('INVALID_MODEL', 'Unknown model ID.')
    return entry


def preflight(cfg, model_id):
    entry = resolve(cfg, model_id)
    try:
        for key, digest_key in [('checkpoint', 'sha256'), ('config', 'config_sha256')]:
            path = cfg.models / entry[key]
            if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.resolve().parent != cfg.models.resolve():
                raise ValueError('Not a regular local file')
            digest = entry.get(digest_key)
            if not digest or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError('Administrator must enroll file SHA-256 hashes')
            size = path.stat().st_size
            if key == 'checkpoint' and size < entry.get('min_bytes', 1000000):
                raise ValueError('Checkpoint is truncated')
            if key == 'config' and not 32 <= size <= 1024 * 1024:
                raise ValueError('Configuration has an invalid size')
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                    raise ValueError('File integrity check failed')
        with zipfile.ZipFile(cfg.models / entry['checkpoint']) as archive:
            if not any(n.endswith('/data.pkl') for n in archive.namelist()) or archive.testzip():
                raise ValueError('Invalid PyTorch checkpoint archive')
        document = yaml.load((cfg.models / entry['config']).read_text(encoding='utf-8'), Loader=ModelConfigLoader)
        if not isinstance(document, dict) or not all(k in document for k in ('audio', 'model', 'training', 'inference')):
            raise ValueError('Invalid RoFormer configuration')
        return entry
    except (OSError, ValueError, zipfile.BadZipFile, yaml.YAMLError) as exc:
        raise Problem('MODEL_NOT_AVAILABLE', f"{entry['name']}: {exc}", 409) from exc
