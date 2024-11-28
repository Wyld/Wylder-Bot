from flask import Flask
from threading import Thread


app = Flask('')


@app.route('/')
def home():
    return "Der Bot läuft!"


def run():
    port = int(os.environ.get("Port", 12000))
    app.run(host='0.0.0.0', port=port)



def keep_alive():
    server = Thread(target=run)
    server.start()