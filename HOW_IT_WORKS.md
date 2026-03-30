# How It Works: RAG Queue Architecture

This document breaks down the underlying architecture of the RAG Queue project, explaining how data flows through the system and why it is structured this way.

## Overview

This project implements a **Retrieval-Augmented Generation (RAG)** system built on a **task queue architecture**. 

Normally, querying a vector database and waiting for an LLM (Large Language Model) to generate a response takes multiple seconds. If we did this directly inside our main web server, the server would be essentially "frozen" for that user and unable to handle multiple requests at the same time. 

To solve this, we split the application into two main compute engines: an **API Server** for fast HTTP responses, and a **Background Worker** for the heavy AI lifting.

---

## Core Components

1. **FastAPI (`server.py`)**: The main web interface. It acts exclusively as a traffic controller, receiving incoming questions and returning statuses instantly without doing any heavy processing.
2. **Valkey (Message Broker)**: An open-source fork of Redis. It acts as the "waiting room" (or state store). It holds jobs in a queue until a worker is ready to process them, and holds the final answers once they are done.
3. **RQ (Redis Queue)**: The Python library (`rq`) we use. It connects to Valkey using the standard Redis protocol and handles pulling/pushing jobs in and out of the database.
4. **Worker (`queues/worker.py`)**: A separate background process/container. It continuously listens to Valkey for new jobs, claims them, and executes the actual RAG pipeline.
5. **Qdrant**: Our separate vector database where our PDF text chunks are already indexed inside the `learning_rag` collection.
6. **Groq (Llama-3.3-70b)**: The external LLM provider that reads our retrieved context and generates the final English response.

---

## Step-by-Step Data Flow

Here is exactly what happens when a user asks a question:

### 1. The User Request
A user sends a POST request with their question (`query`) to the FastAPI server (`POST /chat`). 

### 2. Enqueuing the Job
FastAPI does **not** process the heavy query. Instead, it uses the RQ library to package the `process_query` instruction payload alongside the user's query. It sends this package to the **Valkey** database. 
Valkey stores the job and returns a unique `job_id`. FastAPI immediately returns this `job_id` to the user and closes the connection. 

### 3. Background Processing (Different Compute)
Running in a completely separate Docker container, our **Worker** is constantly polling Valkey, asking, *"Do you have work for me?"*
The Worker detects the new job, claims it, and executes the `process_query` function.

### 4. Vector Retrieval (Semantic Search)
Inside the `process_query` function, the Worker:
* Converts the text query into a mathematical vector using HuggingFace embeddings.
* Connects to **Qdrant** (`http://host.docker.internal:6333`).
* Performs a semantic search to find the text chunks that are most relevant to the question.

### 5. AI Generation
The Worker grabs those retrieved text chunks and bundles them together into a large `context` string. 
It places this context inside a strict `SYSTEM_PROMPT` and ships it off to the **Groq API**. Groq processes the context with the `llama-3.3-70b` model and replies with an answer.

### 6. Saving the Result
The Worker finishes the function and returns the final AI string. The RQ library automatically takes this return value and saves it into **Valkey** under the original `job_id`, marking the status as "finished".

### 7. The Follow-up
The user (or frontend client) sends a request to `GET /job-status?job_id=...`. FastAPI quickly checks Valkey for that specific ID. Since the Worker is done, Valkey provides the answer, and FastAPI serves it back to the user.

---

## Visual Architecture Diagram

```mermaid
graph TD
    subgraph "Docker Compose Network"
        API[FastAPI Server <br> port: 8000]
        Valkey[(Valkey Broker <br> port: 6379)]
        Worker[RQ Background Worker]
    end

    subgraph "External Services"
        Qdrant[(Qdrant Vector DB <br> port: 6333)]
        Groq[Groq API <br> Llama-3.3-70b]
    end

    Client([User / Client]) -- "1. POST /chat" --> API
    API -- "2. Enqueue Job payload" --> Valkey
    API -- "3. Return Job ID" --> Client

    Worker -- "4. Claim Pending Job" --> Valkey
    
    Worker -- "5. Embed & Semantic Search" --> Qdrant
    Qdrant -- "6. Return Text Chunks" --> Worker
    
    Worker -- "7. Send Prompt + Context" --> Groq
    Groq -- "8. Return Generated Answer" --> Worker

    Worker -- "9. Save Result to Job ID" --> Valkey
    
    Client -- "10. GET /job-status" --> API
    API -- "11. Fetch Completed Result" --> Valkey
    Valkey -- "12. Return Data" --> API
    API -- "13. Return Final Answer" --> Client
```

## Why this Architecture?
* **Non-Blocking:** By moving the 5-10 second AI generation step out of the HTTP request cycle, the FastAPI server remains lighting fast and can accept thousands of concurrent requests.
* **Scalable:** If you start getting too many requests, you can simply spin up 5 more Worker containers. They will all connect to the same Valkey queue and process jobs in parallel.
* **Resilient:** If a job fails because the Groq API times out, the queue remembers the job and can easily retry it.

### How the Containers "Talk" to Each Other
Imagine your computer is an office building. By default, when you run `docker-compose up`, Docker creates a private "Virtual Local Area Network" (VLAN) for your application. This is like a private office floor just for your RAG project. 

*   Inside this private office floor, Docker assigns a specific desk (an IP address) to every container defined in your `docker-compose.yml`.
*   Docker has a built-in "receptionist" (an internal DNS server). Instead of your containers having to memorize each other's IP addresses, they just use the **names of the services** written in your `docker-compose.yml`.

So, when your FastAPI container wants to connect to the queue, it doesn't need a complex URL. It just looks for `REDIS_HOST=valkey`. Docker secretly maps the word `valkey` to the exact IP address of the Valkey container on that private floor. 

The `valkey` container acts as the literal middleman. 
1.  **The API connects to Valkey:** When you enqueue a job with RQ, the FastAPI container connects to the `valkey` container and writes "Job X" into the database.
2.  **The Worker connects to Valkey:** The worker container runs the command `rq worker -u redis://valkey:6379`. This tells the worker to also connect to the `valkey` container over that private network.

**Crucially, the API container and the Worker container NEVER talk directly to each other.** The API drops off a package at Valkey, and the Worker picks up a package from Valkey. 

### How "Separate Compute" Actually Works
When we say "separate compute," it means each container is treated by your computer's operating system as an entirely separate process, just like having Google Chrome and Microsoft Word open at the same time.

*   When the **API (`uvicorn`)** is running, it might be utilizing 5% of CPU Core 1.
*   When the **Worker (`rq`)** doing heavy embeddings and Qdrant searching, it might suddenly spike and use 100% of CPU Core 2.

Because they are isolated processes in isolated Docker environments:
1.  If the worker's processing maxes out its CPU core, it *does not slow down* the FastAPI container. FastAPI can continue listening on its own core and answering new requests instantly. 
2.  If the worker somehow crashes due to a bad file or a timeout out of memory error, the FastAPI container stays online.

### Scaling the Workers for the Future
Because the API and the Worker do not talk directly to each other and only talk to Valkey, scaling becomes incredibly easy.

Let's say your app becomes very popular, and suddenly you get 10 users requesting RAG queries at the exact same second.
*   Your API instantly creates 10 jobs in Valkey (`Job 1` through `Job 10`).
*   If you only have **one Worker**, that worker must process `Job 1`, wait 10 seconds for Groq, finish, and then start `Job 2`. The 10th user will wait over a minute for their result!

Because of this decoupled architecture, scaling in the future simply requires telling Docker to spin up more Worker clones. You can do this with a single command:
```bash
docker-compose up -d --scale worker=5
```

If you do this, Docker spins up 5 identical worker containers running identical code. All 5 are given access to your private Docker network, and all 5 connect to `valkey`.

**What happens to those 10 concurrent requests now?**
*   **Worker A** grabs Job 1.
*   **Worker B** grabs Job 2.
*   **Worker C** grabs Job 3... etc.

They all process jobs independently in parallel. Once Worker A finishes Job 1, it immediately asks Valkey for the next available job (Job 6). The 10th user goes from waiting a minute to waiting just 10-15 seconds. You achieved "horizontal scaling" without needing to rewrite a single line of your FastAPI code!