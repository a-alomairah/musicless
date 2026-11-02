import os
from pathlib import Path
import pytest
import yaml


def test_compose_isolation():
    path=Path(__file__).resolve().parents[1]/'compose.yaml'
    if not path.exists(): pytest.skip('Compose source is not copied into this test image')
    document=yaml.safe_load(path.read_text())
    worker=document['services']['worker'];web=document['services']['web']
    assert worker['network_mode']=='none'
    assert any(str(m).endswith(':/models:ro') for m in worker['volumes'])
    assert not any('/models' in str(m) for m in web['volumes'])
    assert 'deploy' not in web
    assert all('docker.sock' not in str(s) for s in document['services'].values())


@pytest.mark.container
def test_actual_network_namespace():
    if os.getenv('ASSERT_NETWORK_NONE')!='1': pytest.skip('Run container with --network none and ASSERT_NETWORK_NONE=1')
    assert {p.name for p in Path('/sys/class/net').iterdir()}=={'lo'}
