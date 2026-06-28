import os
from functools import wraps
from flask import Flask, send_from_directory, abort, request, Response
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

data_dir = os.environ.get("DATA_DIR")
_feed_username = os.environ.get("FEED_USERNAME")
_feed_password = os.environ.get("FEED_PASSWORD")
_episode_token = os.environ.get("EPISODE_TOKEN")


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != _feed_username or auth.password != _feed_password:
            return Response(
                'Unauthorized',
                401,
                {'WWW-Authenticate': 'Basic realm="Skipcastify"'},
            )
        return f(*args, **kwargs)
    return decorated


@app.route('/feeds/<podcast_name>.xml')
@require_auth
def serve_feed(podcast_name):
    path = os.path.join(data_dir, 'feeds')
    filename = f'{podcast_name}.xml'
    if not os.path.exists(os.path.join(path, filename)):
        return abort(404)
    return send_from_directory(path, filename)


@app.route('/episodes/<token>/<podcast_name>/<path:filename>')
def serve_episode(token, podcast_name, filename):
    if token != _episode_token:
        return abort(403)
    for subdir in ('processed', 'raw'):
        path = os.path.join(data_dir, 'podcasts', subdir, podcast_name)
        if os.path.exists(os.path.join(path, filename)):
            return send_from_directory(path, filename)
    return abort(404)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
