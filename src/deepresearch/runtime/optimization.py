"""Optional document-local adjacent evidence selection; never accepts Gold/reference fields."""
class AdjacentEvidenceRetriever:
    backend_name = 'bm25-document-local-neighborhood-v1'

    def __init__(self, base, reader):
        self.base = base; self.reader = reader; self.neighbors = {}
        by_doc = {}
        for chunk in reader.chunks.values(): by_doc.setdefault(chunk.document_id, []).append(chunk.chunk_id)
        for ids in by_doc.values():
            for index, cid in enumerate(ids): self.neighbors[cid] = ids[max(0, index - 1):index] + ids[index + 1:index + 2]

    def search(self, query):
        originals = self.base.search(query); result = []; seen = set()
        for hit in originals:
            for cid in [hit.chunk_id] + self.neighbors[hit.chunk_id]:
                if cid not in seen:
                    document = self.reader.read(cid); document.retrieval_score = hit.retrieval_score
                    result.append(document); seen.add(cid)
                    if len(result) == self.base.top_k: return result
        return result
