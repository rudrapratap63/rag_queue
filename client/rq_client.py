import os
from redis import Redis
from rq import Queue

redis_host = os.environ.get("REDIS_HOST", "localhost")

queue = Queue(connection=Redis(
    host=redis_host,
    port="6379" #type: ignore 
))