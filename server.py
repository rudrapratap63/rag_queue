from fastapi import FastAPI, Query
from queues.worker import process_query
from client.rq_client import queue

app = FastAPI()

@app.get("/")
def home():
    return {"status": "server is running"}

@app.post("/chat")
def chat(query: str = Query(..., description="chat query of user")):
    job = queue.enqueue(process_query, query)
    
    return {"status": "queued", "JOB_ID": job.id}

@app.get("/job-status")
def get_result(
    job_id: str = Query(..., description="Job ID")
):
    job = queue.fetch_job(job_id=job_id)
    if not job: 
        return {"message": "something went wrong"}
     
    result = job.return_value()
    return {"result": result}