import os
from pathlib import Path
import shutil
import pytest
from musicless.config import Problem
from musicless.media import FORMATS, ffmpeg, probe, extract, encode


@pytest.fixture
def media_tools():
    if not shutil.which(os.getenv('FFMPEG','ffmpeg')) or not shutil.which(os.getenv('FFPROBE','ffprobe')):
        pytest.skip('Real FFmpeg and FFprobe required')


def fixture(path, duration=2, video=False, offset=0):
    args=[]
    if video: args+=['-f','lavfi','-i',f'color=c=blue:s=160x120:r=25:d={duration}']
    if offset: args+=['-itsoffset',str(offset)]
    args+=['-f','lavfi','-i',f'sine=frequency=440:sample_rate=44100:duration={duration-offset}','-ac','2']
    if video:
        args+=['-c:v','libvpx-vp9' if path.suffix=='.webm' else 'libx264']
    args+=['-c:a',FORMATS[path.suffix][1],str(path)]
    ffmpeg(args,30)


@pytest.mark.integration
@pytest.mark.parametrize('ext',list(FORMATS))
def test_real_container_roundtrip(cfg,media_tools,tmp_path,ext):
    source=tmp_path/('source'+ext)
    fixture(source,video=ext in ('.mp4','.mov','.mkv','.webm'))
    meta=probe(source,ext,cfg)
    wav=tmp_path/'extracted.wav'
    extract(source,wav,meta,cfg)
    output=tmp_path/('source_musicless'+ext)
    # Lossless extracted audio stands in for the vocal stem here. No GPU claim.
    encode(source,wav,output,ext,meta,cfg)
    result=probe(output,ext,cfg)
    assert output.suffix==ext
    assert result['video']==meta['video']
    assert abs(result['duration']-meta['duration'])<.25


@pytest.mark.integration
def test_wrong_extension_duration_corruption(cfg,media_tools,tmp_path):
    source=tmp_path/'test.wav'
    fixture(source)
    with pytest.raises(Problem): probe(source,'.mp3',cfg)
    cfg.duration=1
    with pytest.raises(Problem): probe(source,'.wav',cfg)
    source.write_bytes(b'not media')
    with pytest.raises(Problem): probe(source,'.wav',cfg)


@pytest.mark.integration
def test_full_duration_over_one_minute(cfg,media_tools,tmp_path):
    cfg.budget=100*1024**2
    source=tmp_path/'long.mp4'
    fixture(source,duration=65,video=True)
    meta=probe(source,'.mp4',cfg)
    wav=tmp_path/'audio.wav'
    extract(source,wav,meta,cfg)
    output=tmp_path/'long_musicless.mp4'
    encode(source,wav,output,'.mp4',meta,cfg)
    assert probe(output,'.mp4',cfg)['duration']>=65


@pytest.mark.integration
def test_delayed_audio_alignment(cfg,media_tools,tmp_path):
    import array
    from musicless.media import command
    source=tmp_path/'delayed.mp4'
    fixture(source,duration=3,video=True,offset=1)
    meta=probe(source,'.mp4',cfg)
    wav=tmp_path/'audio.wav'
    extract(source,wav,meta,cfg)
    raw=command([os.getenv('FFMPEG','ffmpeg'),'-v','error','-i',str(wav),'-f','f32le','-ac','1','-'])
    samples=array.array('f',raw)
    assert max(abs(x) for x in samples[:22050])<.001
    assert max(abs(x) for x in samples[66150:88200])>.01


@pytest.mark.gpu
def test_real_gpu_separation(tmp_path):
    if os.getenv('RUN_GPU_TESTS')!='1': pytest.skip('Set RUN_GPU_TESTS=1 inside offline GPU worker image')
    import torch
    from musicless.config import Config
    from musicless.offline import separate
    from musicless import offline
    from musicless.models import preflight
    assert torch.cuda.is_available(), 'GPU integration requires CUDA; CPU is not a substitute'
    cfg=Config()
    before={p.name:(p.stat().st_size,p.stat().st_mtime_ns) for p in cfg.models.iterdir()}
    preflight(cfg,'big-beta-4')
    source=tmp_path/'synthetic.mp4'
    fixture(source,video=True)
    meta=probe(source,'.mp4',cfg)
    wav=tmp_path/'audio.wav';extract(source,wav,meta,cfg)
    stems=tmp_path/'stems';stems.mkdir()
    vocals=separate(cfg,'big-beta-4',wav,stems)
    assert offline.NETWORK_ATTEMPTS==0, 'The separator attempted a forbidden network operation'
    output=tmp_path/'synthetic_musicless.mp4'
    encode(source,vocals,output,'.mp4',meta,cfg)
    assert probe(output,'.mp4',cfg)['video']
    assert before=={p.name:(p.stat().st_size,p.stat().st_mtime_ns) for p in cfg.models.iterdir()}
