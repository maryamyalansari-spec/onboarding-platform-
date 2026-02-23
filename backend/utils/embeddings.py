"""
embeddings.py — pgvector embedding generation and similarity search.
Stub — implemented in Step 12 (conflict check).
"""


def get_embedding(text: str) -> list:
    """
    Generate a 1536-dim embedding vector for text.
    Delegates to ai.py — isolated AI provider call.
    Stub — Step 12.
    """
    from routes.ai import generate_embedding
    return generate_embedding(text)


def cosine_similarity_search(embedding: list, firm_id: str, threshold: float = 0.85, limit: int = 10):
    """
    Search conflict_index for records whose name_embedding has cosine
    similarity >= threshold to the provided embedding.
    Returns list of (record_id, full_name, similarity_score).
    Stub — Step 12.
    """
    raise NotImplementedError("Vector similarity search — implemented in Step 12.")
