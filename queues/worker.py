from langchain_qdrant import QdrantVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
from openai import OpenAI
import os

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)


embedding_model = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-l6-v2"
)


qdrant_url = os.getenv("QDRANT_URL", "http://host.docker.internal:6333")

vector_store = QdrantVectorStore.from_existing_collection(
    collection_name="learning_rag",
    embedding=embedding_model,
    url=qdrant_url
)

def process_query(query: str):
    print("searching chunks for query: ", query)
    search_results = vector_store.similarity_search(query=query)

    context = "\n\n\n".join([f"Page Content: {result.page_content}\nPage Number: {result.metadata['page_label']}\nFile Location: {result.metadata['source']}" for result in search_results])

    SYSTEM_PROMPT = f"""
        You are a helpful assistant who answers user query based on the available context retrieved from a PDF
        file along with page_contents and page number.

        You should ans the user based on the following context and navigate the user to open the right 
        page number to know more.

        Context:
        {context}
    """
    
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query}
        ],
    )

    print(f"🤖: {response.choices[0].message.content}") 
    return response.choices[0].message.content 