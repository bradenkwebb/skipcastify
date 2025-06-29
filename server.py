import os
from flask import Flask, send_from_directory, abort
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

data_dir = os.environ.get("DATA_DIR")

@app.route('/feeds/<podcast_name>.xml')
def serve_feed(podcast_name):
    path = os.path.join(f'{data_dir}/feeds')
    filename = f'{podcast_name}.xml'
    if not os.path.exists(os.path.join(path, filename)):
        return abort(404)
    return send_from_directory(path, filename)

@app.route('/episodes/<podcast_name>/<path:filename>')
def serve_episode(podcast_name, filename):
    path = os.path.join(f'{data_dir}/podcasts', podcast_name)
    if not os.path.exists(os.path.join(path, filename)):
        return abort(404)
    return send_from_directory(path, filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
