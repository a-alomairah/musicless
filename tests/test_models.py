import hashlib
import json

import pytest
import yaml

from musicless.config import Problem
from musicless.models import ModelConfigLoader, preflight


def enroll_config(cfg, model_yaml):
    (cfg.models / 'test.yaml').write_text(model_yaml, encoding='utf-8')
    document = json.loads(cfg.registry.read_text())
    document['models'][0]['config_sha256'] = hashlib.sha256(model_yaml.encode()).hexdigest()
    cfg.registry.write_text(json.dumps(document))


def test_roformer_tuple_config_passes_preflight(cfg):
    model_yaml = '''audio: {sample_rate: 44100}
model:
  multi_stft_resolutions_window_sizes: !!python/tuple [4096, 2048, 1024, 512, 256]
training: {}
inference: {}
'''
    enroll_config(cfg, model_yaml)
    assert preflight(cfg, 'big-beta-4')['id'] == 'big-beta-4'
    document = yaml.load(model_yaml, Loader=ModelConfigLoader)
    assert document['model']['multi_stft_resolutions_window_sizes'] == (4096, 2048, 1024, 512, 256)
    # The custom constructor must not change the global safe loader.
    with pytest.raises(yaml.constructor.ConstructorError):
        yaml.safe_load(model_yaml)


@pytest.mark.parametrize('value', [
    '!!python/object/apply:builtins.eval ["1 + 1"]',
    '!!python/object:builtins.object {}',
    '!!python/name:builtins.eval',
    '!!python/tuple {invalid: mapping}',
    '!!python/tuple [!!python/object/apply:builtins.eval ["1 + 1"]]',
])
def test_model_config_rejects_unsafe_tags_and_invalid_tuples(cfg, value):
    enroll_config(cfg, f'audio: {{}}\nmodel: {{value: {value}}}\ntraining: {{}}\ninference: {{}}\n')
    with pytest.raises(Problem) as error:
        preflight(cfg, 'big-beta-4')
    assert error.value.code == 'MODEL_NOT_AVAILABLE'


def test_tuple_config_still_requires_matching_hash(cfg):
    (cfg.models / 'test.yaml').write_text('audio: {}\nmodel: {sizes: !!python/tuple [1, 2]}\ntraining: {}\ninference: {}\n')
    with pytest.raises(Problem, match='File integrity check failed'):
        preflight(cfg, 'big-beta-4')
