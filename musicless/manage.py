"""Explicit, offline host administration. Never invoked by HTTP endpoints."""
import argparse
import getpass
import hashlib
import json
from pathlib import Path
import secrets
from werkzeug.security import generate_password_hash
from .models import resolve


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('credentials')
    enroll=sub.add_parser('enroll')
    enroll.add_argument('model_id')
    enroll.add_argument('--models',type=Path,default=Path('models'))
    enroll.add_argument('--registry',type=Path,default=Path('config/models.json'))
    args=parser.parse_args()
    if args.command=='credentials':
        password=getpass.getpass('Admin password (at least 12 characters): ')
        if len(password)<12 or password!=getpass.getpass('Repeat password: '):
            parser.error('Passwords must match and contain at least 12 characters.')
        print('ADMIN_PASSWORD_HASH='+generate_password_hash(password))
        print('SESSION_SECRET='+secrets.token_hex(32))
    else:
        # This explicit command records trust in existing administrator-supplied files.
        class Local:
            registry=args.registry
            models=args.models
        entry=resolve(Local,args.model_id)
        document=json.loads(args.registry.read_text())
        for key,digest_key in [('checkpoint','sha256'),('config','config_sha256')]:
            path=args.models/entry[key]
            if path.is_symlink() or not path.is_file() or path.resolve().parent!=args.models.resolve():
                parser.error('Model files must be regular files inside the models directory.')
            with path.open('rb') as stream:
                entry[digest_key]=hashlib.file_digest(stream,'sha256').hexdigest()
        document['models']=[entry if e['id']==args.model_id else e for e in document['models']]
        args.registry.write_text(json.dumps(document,indent=2)+'\n')
        print('Recorded local hashes. Worker preflight will validate size, archive and YAML structure.')


if __name__=='__main__':
    main()
