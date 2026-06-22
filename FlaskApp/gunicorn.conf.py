bind = "0.0.0.0:8000"

workers = 6
worker_class = "gthread"
threads = 20

timeout = 180
graceful_timeout = 30
keepalive = 15

accesslog = "-"
errorlog = "-"
loglevel = "info"
capture_output = True
worker_tmp_dir = "/dev/shm"

max_requests = 1500
max_requests_jitter = 200