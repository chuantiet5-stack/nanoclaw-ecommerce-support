"""RAG 知识库：商品政策、FAQ、售后条款的语义检索（生产落地 P2-3.3）。

依赖（较重，按需安装）：
    pip install chromadb sentence-transformers rank-bm25

本模块采用惰性导入：未安装依赖时 import 本模块不报错，
仅在实例化 KnowledgeBase 时给出明确提示。
当前尚未接入 agent/context.py（接入属于行为变更，待知识库数据就绪后启用）。
"""

import os


class KnowledgeBase:
    """向量知识库（Chroma + SentenceTransformers）。"""

    def __init__(self, persist_dir: str = "data/chroma",
                 embed_model: str = "all-MiniLM-L6-v2"):
        try:
            import chromadb
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "RAG 知识库依赖未安装，请执行："
                "pip install chromadb sentence-transformers"
            ) from e

        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection(
            name="ecom_knowledge",
            metadata={"hnsw:space": "cosine"},
        )
        self.model = SentenceTransformer(embed_model)

    def add_documents(self, documents: list[dict]):
        """批量添加/更新文档。

        documents: [{"id": "faq_001", "text": "...", "category": "FAQ"}, ...]
        """
        ids = [d["id"] for d in documents]
        texts = [d["text"] for d in documents]
        metadatas = [{"category": d.get("category", "")} for d in documents]
        embeddings = self.model.encode(texts).tolist()

        self.collection.upsert(
            ids=ids,
            documents=texts,
            metadatas=metadatas,
            embeddings=embeddings,
        )

    def query(self, question: str, top_k: int = 3) -> list[dict]:
        """语义检索相关知识。"""
        query_embedding = self.model.encode([question]).tolist()
        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=top_k,
        )
        return [
            {"text": doc, "category": meta.get("category", ""), "distance": dist}
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]
