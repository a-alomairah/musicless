import hmac
import os
from pathlib import Path
import secrets
import time
from datetime import timedelta
from flask import Flask, jsonify, request, session, render_template, redirect, send_file
from werkzeug.security import check_password_hash
from werkzeug.exceptions import HTTPException
from . import db
from .config import Config, Problem
from .models import registry, resolve
from .media import FORMATS
from .i18n import ARABIC, language, translate, detail


def create_app(cfg=None):
    cfg = cfg or Config()
    db.init(cfg)
    app = Flask(__name__)
    secret = os.environ.get('SESSION_SECRET','')
    password_hash = os.environ.get('ADMIN_PASSWORD_HASH','')
    if len(secret) < 32 or not password_hash:
        raise RuntimeError('Set SESSION_SECRET and ADMIN_PASSWORD_HASH before starting.')
    app.config.update(SECRET_KEY=secret, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE','true').lower()=='true', PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=cfg.max_upload)

    def error_response(code, message, status):
        if request.path.startswith('/api/'):
            return jsonify(error=code, message=message), status
        return render_template('error.html', message=message), status

    @app.errorhandler(Problem)
    def problem(exc):
        return error_response(exc.code, detail(exc.message, exc.code), exc.status)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        message = {404: 'Page not found.', 405: 'This request method is not supported.'}.get(exc.code, 'Request failed.')
        response = app.make_response(error_response(exc.name, translate(message), exc.code))
        if exc.code == 405:
            response.headers['Allow'] = ', '.join(exc.valid_methods or [])
        return response

    @app.errorhandler(413)
    def too_large(exc):
        return error_response('UPLOAD_TOO_LARGE', detail('Upload exceeds the configured size limit.', 'UPLOAD_TOO_LARGE'), 413)

    def owner():
        return session.get('user') or session['visitor']

    @app.before_request
    def protect():
        if request.path == '/healthz':
            return None
        if request.path.startswith('/static/'):
            return None
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        if 'visitor' not in session:
            session['visitor'] = 'visitor:' + secrets.token_urlsafe(32)
            session.permanent = True
        db.rate(cfg, 'read:' + owner(), 240)
        if request.method not in ('GET','HEAD','OPTIONS'):
            csrf = request.headers.get('X-CSRF-Token','')
            if request.path in ('/login','/logout','/language'):
                if request.content_length and request.content_length > 8192:
                    raise Problem('INVALID_REQUEST','Request too large.',413)
                csrf = request.form.get('csrf','')
            if not hmac.compare_digest(csrf, session['csrf']):
                raise Problem('CSRF','Invalid CSRF token.',403)
            db.rate(cfg, 'write:'+session.get('user',request.remote_addr or 'local'),30)

    @app.after_request
    def headers(response):
        response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    @app.route('/login',methods=['GET','POST'])
    def login():
        error = None
        if request.method=='POST':
            db.rate(cfg,'login:'+ (request.remote_addr or 'local'),5,300)
            good_user = hmac.compare_digest(request.form.get('username',''),os.getenv('ADMIN_USERNAME','admin'))
            good_password = check_password_hash(password_hash,request.form.get('password',''))
            if good_user and good_password:
                visitor = session['visitor']
                preferred_language = language()
                session.clear()
                session['language'] = preferred_language
                session['visitor'] = visitor
                session.update(user=os.getenv('ADMIN_USERNAME','admin'), role='admin', csrf=secrets.token_urlsafe(32))
                session.permanent = True
                return redirect('/')
            error = 'Invalid username or password.'
        return render_template('login.html',error=error)

    @app.post('/logout')
    def logout():
        visitor = session['visitor']
        preferred_language = language()
        session.clear()
        session['language'] = preferred_language
        session.update(visitor=visitor, csrf=secrets.token_urlsafe(32))
        session.permanent = True
        return redirect('/')

    @app.context_processor
    def localization():
        return {'t': translate, 'language': language(), 'translations': ARABIC if language() == 'ar' else {}}

    @app.post('/language')
    def change_language():
        selected = request.form.get('language')
        if selected not in ('ar', 'en'):
            raise Problem('INVALID_REQUEST', 'Invalid language.')
        session['language'] = selected
        destination = request.form.get('next', '/')
        return redirect(destination if destination in ('/', '/login', '/settings', '/privacy') else '/')

    @app.get('/privacy')
    def privacy_page():
        return render_template('privacy.html', retention=cfg.retention, timeout=cfg.timeout)

    @app.get('/')
    def index():
        return render_template('index.html',limit=cfg.max_upload//1024**2,duration=cfg.duration)

    @app.get('/settings')
    def settings_page():
        admin()
        return render_template('settings.html')

    def admin():
        if session.get('role')!='admin':
            raise Problem('FORBIDDEN','Administrator access required.',403)

    def status():
        worker = db.setting(cfg,'worker',{})
        worker['healthy'] = time.time()-worker.get('heartbeat',0)<30
        return {'worker':worker,'models':list(registry(cfg).values()),'default_model':db.setting(cfg,'default_model'),'allow_choice':db.setting(cfg,'allow_choice')}

    def available(model_id):
        resolve(cfg,model_id)
        info = status()['worker']
        if not info['healthy']:
            raise Problem('WORKER_UNAVAILABLE','Worker is offline or starting.',503)
        # Synchronous worker preflight before reading the upload body. The web has no model mount.
        requested = time.time()
        db.put(cfg,'preflight_request',requested)
        deadline = time.monotonic()+90
        while time.monotonic()<deadline:
            result = db.setting(cfg,'availability',{})
            if result.get('checked_at',0)>=requested:
                model = result.get('models',{}).get(model_id,{})
                if not model.get('available'):
                    raise Problem('MODEL_NOT_AVAILABLE',model.get('reason','Model unavailable.'),409)
                if not info.get('ready'):
                    raise Problem('CUDA_UNAVAILABLE','Worker GPU is unavailable; inspect settings.',503)
                return
            time.sleep(.2)
        raise Problem('WORKER_UNAVAILABLE','Model preflight timed out.',503)

    @app.get('/api/status')
    def api_status():
        result = status()
        result['availability'] = db.setting(cfg,'availability',{})
        for model in result['availability'].get('models', {}).values():
            model['reason'] = detail(model.get('reason', ''), 'MODEL_NOT_AVAILABLE')
        # The administrator-maintained registry is not mutable through the API.
        result['models'] = [{'id':m['id'],'name':m['name']} for m in result['models']]
        return jsonify(result)

    @app.post('/api/settings')
    def settings_save():
        admin()
        if request.content_length is None or request.content_length>4096:
            raise Problem('INVALID_REQUEST','Invalid settings request.')
        body = request.get_json(silent=True)
        if not isinstance(body,dict) or set(body)!={'default_model','allow_choice'} or type(body['allow_choice']) is not bool:
            raise Problem('INVALID_REQUEST','Only default_model and allow_choice are accepted.')
        available(body['default_model'])
        db.put(cfg,'default_model',body['default_model'])
        db.put(cfg,'allow_choice',body['allow_choice'])
        return jsonify(ok=True)

    @app.post('/api/jobs')
    def upload():
        if request.mimetype!='application/octet-stream' or set(request.args)-{'filename','model_id'} or any(len(request.args.getlist(k))!=1 for k in request.args):
            raise Problem('INVALID_REQUEST','Send raw media with filename and an approved model_id only.')
        filename = request.args.get('filename','')
        if not filename or len(filename)>180 or any(ord(c)<32 or c in '/\\:%' for c in filename) or filename.startswith('-'):
            raise Problem('INVALID_FILENAME','Use a simple filename without paths or command options.')
        ext = Path(filename).suffix.lower()
        if ext not in FORMATS:
            raise Problem('UNSUPPORTED_FORMAT','Supported: '+', '.join(FORMATS))
        model_id = request.args.get('model_id', db.setting(cfg,'default_model'))
        resolve(cfg,model_id)
        if not db.setting(cfg,'allow_choice') and model_id!=db.setting(cfg,'default_model'):
            raise Problem('MODEL_CHOICE_DISABLED','Use the default model.')
        available(model_id)
        ident = db.reserve(cfg,owner(),model_id,filename,ext)
        try:
            total = 0
            with (cfg.data/'jobs'/ident/('input'+ext)).open('xb') as out:
                while chunk := request.stream.read(1024*1024):
                    total += len(chunk)
                    if total>cfg.max_upload:
                        raise Problem('UPLOAD_TOO_LARGE','Upload exceeds the size limit.',413)
                    out.write(chunk)
                    db.update(cfg,ident,stage='Receiving upload')
            if total==0:
                raise Problem('INVALID_MEDIA','The upload is empty.')
            db.update(cfg,ident,state='queued',stage='Queued for validation')
        except Exception:
            db.update(cfg,ident,state='failed',error='UPLOAD_FAILED')
            db.cleanup(cfg)
            raise
        return jsonify(id=ident,state='queued'),202

    @app.get('/api/jobs')
    def jobs():
        with db.connect(cfg) as conn:
            rows=conn.execute('SELECT * FROM jobs WHERE owner=? ORDER BY created DESC LIMIT 30',(owner(),)).fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            row['stage'] = detail(row['stage'], row['error'])
        return jsonify(result)

    @app.post('/api/jobs/<ident>/cancel')
    def cancel(ident):
        row=db.get(cfg,ident,owner())
        if row['state'] in ('queued','processing'):
            db.update(cfg,ident,cancel=1)
        return jsonify(ok=True)

    @app.get('/api/jobs/<ident>/download')
    def download(ident):
        row=db.get(cfg,ident,owner())
        if row['state']!='complete' or row['expires']<=time.time():
            raise Problem('NOT_FOUND','Output is unavailable or expired.',404)
        name=Path(row['filename']).stem+'_musicless'+row['ext']
        return send_file(cfg.data/'jobs'/ident/('output'+row['ext']),as_attachment=True,download_name=name,conditional=True)

    @app.get('/healthz')
    def health():
        return jsonify(web='healthy')

    return app
