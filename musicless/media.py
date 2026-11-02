import json
import math
import os
from pathlib import Path
import subprocess
from .config import Problem

# extension: probe family, output audio codec, video allowed
FORMATS = {
    '.mp4': ({'mov','mp4'}, 'aac', True), '.mov': ({'mov','mp4'}, 'aac', True),
    '.mkv': ({'matroska'}, 'aac', True), '.webm': ({'webm','matroska'}, 'libopus', True),
    '.mp3': ({'mp3'}, 'libmp3lame', False), '.wav': ({'wav'}, 'pcm_s16le', False),
    '.flac': ({'flac'}, 'flac', False), '.m4a': ({'mov','mp4'}, 'aac', False),
    '.ogg': ({'ogg'}, 'libvorbis', False),
}
AUDIO = {'aac','mp3','opus','vorbis','flac','pcm_s16le','pcm_s24le','pcm_s32le','pcm_f32le','pcm_f64le','alac','ac3','eac3'}
VIDEO = {'.mp4': {'h264','hevc','av1','mpeg4'}, '.mov': {'h264','hevc','prores','mpeg4'}, '.webm': {'vp8','vp9','av1'}, '.mkv': {'h264','hevc','av1','vp8','vp9','mpeg4','mpeg2video','prores'}}


def command(args, timeout=60):
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=True)
        return result.stdout
    except (subprocess.SubprocessError, OSError) as exc:
        raise Problem('MEDIA_FAILED', 'The media could not be read or encoded.') from exc


def probe(path, ext, cfg, output=False):
    if ext not in FORMATS:
        raise Problem('UNSUPPORTED_FORMAT', 'Unsupported file extension.')
    if Path(path).stat().st_size > cfg.max_upload and Path(path).name.startswith('input'):
        raise Problem('UPLOAD_TOO_LARGE', 'Upload exceeds the size limit.')
    raw = command([os.getenv('FFPROBE','ffprobe'), '-v','error','-protocol_whitelist','file,pipe','-show_format','-show_streams','-of','json', str(path)], 20)
    try:
        data = json.loads(raw)
        fmt, streams = data['format'], data['streams']
        if not FORMATS[ext][0].intersection(fmt['format_name'].split(',')):
            raise ValueError('The extension does not match the actual container.')
        # FFprobe groups ISO-BMFF and Matroska/WebM; inspect their container identifiers too.
        with Path(path).open('rb') as source:
            head = source.read(4096)
        if ext == '.webm' and b'webm' not in head:
            raise ValueError('The file is not a WebM container.')
        if ext == '.mov' and fmt.get('tags', {}).get('major_brand', '').strip() != 'qt':
            raise ValueError('The file is not a QuickTime MOV container.')
        if ext in ('.mp4','.m4a') and fmt.get('tags', {}).get('major_brand','').strip() == 'qt':
            raise ValueError('QuickTime content requires a .mov extension.')
        audio = [s for s in streams if s['codec_type']=='audio']
        video = [s for s in streams if s['codec_type']=='video']
        if len(audio)!=1 or len(video)>1 or any(s['codec_type'] not in ('audio','video') for s in streams):
            raise ValueError('Use one audio track, at most one video track, and no subtitles, attachments or data tracks.')
        if video and (not FORMATS[ext][2] or video[0]['codec_name'] not in VIDEO[ext] or video[0].get('disposition',{}).get('attached_pic')):
            raise ValueError('Unsupported video codec or embedded cover art.')
        if video and (int(video[0].get('width',0))*int(video[0].get('height',0)) > 3840*2160):
            raise ValueError('Video resolution exceeds 3840×2160 pixels.')
        a = audio[0]
        if a['codec_name'] not in AUDIO or int(a.get('channels',0)) not in (1,2) or not 8000 <= int(a.get('sample_rate',0)) <= 96000:
            raise ValueError('Use supported mono/stereo audio at 8–96 kHz.')
        duration = float(fmt['duration'])
        limit=cfg.duration + (.25 if output else 0)
        if not math.isfinite(duration) or not 0 < duration <= limit:
            raise ValueError(f'Media must be no longer than {cfg.duration:g} seconds.')
        for stream in streams:
            if stream.get('duration') not in (None,'N/A'):
                stream_duration=float(stream['duration'])
                if not math.isfinite(stream_duration) or stream_duration>limit+.1:
                    raise ValueError('A media stream exceeds the duration limit.')
        origin = float(fmt.get('start_time',0))
        offset = float(a.get('start_time',origin))-origin
        if not math.isfinite(offset) or offset < -0.05 or offset >= duration:
            raise ValueError('Unsupported audio timestamps.')
        if duration*44100*2*4*3 + Path(path).stat().st_size*2 > cfg.budget:
            raise ValueError('Decoded media exceeds the per-job storage budget.')
        return {'duration':duration,'offset':max(0,offset),'video':bool(video),'video_codec':video[0]['codec_name'] if video else None}
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise Problem('INVALID_MEDIA', str(exc)) from exc


def ffmpeg(args, timeout):
    return command([os.getenv('FFMPEG','ffmpeg'),'-hide_banner','-loglevel','error','-nostdin','-y','-threads','2',*args], timeout)


def extract(source, target, meta, cfg):
    ffmpeg(['-protocol_whitelist','file,pipe','-i',str(source),'-map','0:a:0','-vn','-af',f"aresample=44100:async=1:first_pts=0,apad,atrim=duration={meta['duration']}",'-ac','2','-ar','44100','-c:a','pcm_f32le',str(target)], cfg.timeout)


def encode(source, vocals, target, ext, meta, cfg):
    args = ['-protocol_whitelist','file,pipe','-i',str(source),'-protocol_whitelist','file,pipe','-i',str(vocals)]
    if meta['video']:
        args += ['-map','0:v:0','-c:v','copy']
    args += ['-map','1:a:0','-map_metadata','-1','-map_chapters','-1','-af',f"apad,atrim=duration={meta['duration']}",'-c:a',FORMATS[ext][1]]
    if ext in ('.mp4','.mov','.m4a'):
        args += ['-movflags','+faststart']
    args += [str(target)]
    ffmpeg(args, cfg.timeout)
    final = probe(target, ext, cfg, output=True)
    if abs(final['duration']-meta['duration']) > 0.25 or final['video'] != meta['video'] or final['video_codec'] != meta['video_codec']:
        raise Problem('OUTPUT_INVALID', 'Output duration or streams did not match the input.')
    # Decode the complete result to verify readability, including stream-copied video.
    ffmpeg(['-protocol_whitelist','file,pipe','-i',str(target),'-f','null','-'], cfg.timeout)
