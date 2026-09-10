import signal, threading, logging
from wsgiref.simple_server import make_server
from .config import Settings
from .application import create_app

SHUTDOWN_TIMEOUT = 5.0

def request_shutdown(server, stop_event, timeout=SHUTDOWN_TIMEOUT, wait=True):
    """Start shutdown outside serve_forever; optionally wait only in safe callers."""
    if stop_event.is_set():
        return False
    stop_event.set()
    finished = threading.Event()
    def worker():
        try:
            server.shutdown()
        finally:
            finished.set()
    thread = threading.Thread(target=worker, name="graceful-shutdown", daemon=True)
    thread.start()
    if not wait:
        return thread
    thread.join(timeout)
    if not finished.is_set():
        logging.getLogger("resource_platform").error("shutdown timeout")
    return finished.is_set()

def main():
    settings=Settings.from_env(); app=create_app(settings)
    server=make_server(settings.host, settings.port, app)
    stop_event=threading.Event(); shutdown_threads=[]
    def stop(signum, frame):
        if not stop_event.is_set():
            shutdown_threads.append(request_shutdown(server, stop_event, wait=False))
    signal.signal(signal.SIGINT, stop); signal.signal(signal.SIGTERM, stop)
    print(f"Listening on http://{settings.host}:{settings.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        for thread in shutdown_threads:
            thread.join(SHUTDOWN_TIMEOUT)
        server.server_close()
if __name__ == "__main__": main()
