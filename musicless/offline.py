"""Only the MDXC/RoFormer local adapter is supported. Fail closed on other loaders."""
import socket
from pathlib import Path
from .models import preflight
from .config import Problem

NETWORK_ATTEMPTS = 0

def deny_network():
    def denied(*args, **kwargs):
        global NETWORK_ATTEMPTS
        NETWORK_ATTEMPTS += 1
        raise RuntimeError('Musicless worker prohibits network access')
    socket.create_connection = denied
    socket.getaddrinfo = denied
    socket.socket.connect = denied
    socket.socket.connect_ex = denied


def local_separator_class(base, cfg, model_id):
    class LocalSeparator(base):
        def download_model_files(self, model_filename):
            entry = preflight(cfg, model_id)
            if model_filename != entry['checkpoint']:
                raise Problem('INVALID_MODEL', 'Unapproved checkpoint.')
            return (entry['checkpoint'], 'MDXC', entry['name'], str(cfg.models / entry['checkpoint']), str(cfg.models / entry['config']))

        def download_file_if_not_exists(self, *args, **kwargs):
            raise RuntimeError('Downloads are disabled')

        def list_supported_model_files(self, *args, **kwargs):
            raise RuntimeError('Online catalog is disabled')

        def load_model_data_using_hash(self, *args, **kwargs):
            raise RuntimeError('Online metadata is disabled')
    return LocalSeparator


def separate(cfg, model_id, source, output):
    entry = preflight(cfg, model_id)  # before importing or constructing Audio Separator
    deny_network()
    import torch
    if not torch.cuda.is_available() and not cfg.cpu:
        raise Problem('CUDA_UNAVAILABLE', 'CUDA is unavailable. CPU fallback is disabled.')
    from audio_separator.separator import Separator
    cls = local_separator_class(Separator, cfg, model_id)
    separator = cls(model_file_dir=str(cfg.models), output_dir=str(output), output_format='WAV', output_single_stem='Vocals', use_soundfile=True, mdxc_params={'segment_size':256,'override_model_segment_size':False,'batch_size':1,'overlap':None,'pitch_shift':0})
    separator.load_model(entry['checkpoint'])
    if not cfg.cpu and (separator.torch_device.type != 'cuda' or separator.model_instance.torch_device.type != 'cuda'):
        raise Problem('CUDA_UNAVAILABLE', 'The model did not select CUDA; CPU fallback is disabled.')
    paths = separator.separate(str(source))
    if len(paths) != 1:
        raise Problem('OUTPUT_INVALID', 'The model did not return one vocal stem.')
    path = Path(paths[0])
    if not path.is_absolute():
        path = output / path
    if path.is_symlink() or path.resolve().parent != output.resolve() or not path.is_file():
        raise Problem('OUTPUT_INVALID', 'Invalid vocal stem output.')
    return path
