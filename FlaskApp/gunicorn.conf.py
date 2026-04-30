bind = "0.0.0.0:8000"

workers = 2
worker_class = "gthread"
threads = 30

timeout = 120
graceful_timeout = 30
keepalive = 10

accesslog = "-"
errorlog = "-"
loglevel = "info"
capture_output = True
worker_tmp_dir = "/dev/shm"

max_requests = 2000
max_requests_jitter = 200