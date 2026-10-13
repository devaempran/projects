"""Serve the production app on a given port with a chosen server. usage: serve.py flask|waitress PORT"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from decisionlm import server as A, model
kind, port = sys.argv[1], int(sys.argv[2])
model.warmup()
if kind == "flask":
    A.create_app().run(host="127.0.0.1", port=port, debug=False)          # threaded=True by default, like app.py
else:
    from waitress import serve
    serve(A.create_app(), host="127.0.0.1", port=port, threads=1)         # one worker thread = serialised queue
