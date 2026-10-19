"""
RAG pipeline: Load PDF -> Split -> Embed/Store -> Retrieve -> Answer.

The vector store is cached per file so the PDF is only embedded once.
"""
import os

from langchain_community.document_loaders import PyPDFLoader
from langchain_core.prompts import PromptTemplate
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

TEMPLATE = """Use the following pieces of context to answer the question at the end.
If you don't know the answer, just say that you don't know, don't try to make up an answer.
Use three sentences maximum and keep the answer as concise as possible.

{context}
Question: {question}
Helpful Answer:"""

# file path -> (mtime, vector store)
_store_cache: dict[str, tuple[float, InMemoryVectorStore]] = {}


def _api_kwargs() -> dict:
    api_key = os.getenv("OPENAI_API_KEY")
    return {"api_key": api_key} if api_key else {}


async def _get_store(file_path: str) -> InMemoryVectorStore:
    mtime = os.path.getmtime(file_path)
    cached = _store_cache.get(file_path)
    if cached and cached[0] == mtime:
        return cached[1]

    docs = await PyPDFLoader(file_path).aload()
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=0)
    split_docs = splitter.split_documents(docs)
    store = await InMemoryVectorStore.afrom_documents(
        split_docs, OpenAIEmbeddings(**_api_kwargs())
    )
    _store_cache[file_path] = (mtime, store)
    return store


def forget(file_path: str) -> None:
    _store_cache.pop(file_path, None)


async def chat(file_path: str, query: str) -> dict:
    """Return {"text": answer, "sources": [{"page": int|None, "snippet": str}]}."""
    store = await _get_store(file_path)
    relevant_docs = await store.as_retriever().ainvoke(query)
    context = "\n\n".join(d.page_content for d in relevant_docs)

    model = ChatOpenAI(model="gpt-5", **_api_kwargs())
    prompt = PromptTemplate.from_template(TEMPLATE).format(context=context, question=query)
    response = await model.ainvoke(prompt)

    sources = []
    for d in relevant_docs:
        page = d.metadata.get("page")
        sources.append(
            {
                "page": page + 1 if isinstance(page, int) else None,  # 1-based for humans
                "snippet": " ".join(d.page_content.split())[:300],
            }
        )
    return {"text": response.content, "sources": sources}


DIRECT_TEMPLATE = """You are AskScout, a helpful assistant. Answer the question clearly and concisely
in markdown. If you are unsure or the answer depends on recent events, say so.

Question: {question}
Answer:"""


async def ask(file_path: str | None, query: str) -> dict:
    """Ask mode: RAG over the uploaded PDF, or a direct LLM answer if there's no document."""
    if file_path and os.path.exists(file_path):
        result = await chat(file_path, query)
        return {**result, "mode": "document"}
    model = ChatOpenAI(model="gpt-5", **_api_kwargs())
    response = await model.ainvoke(PromptTemplate.from_template(DIRECT_TEMPLATE).format(question=query))
    return {"text": response.content, "sources": [], "mode": "general"}
