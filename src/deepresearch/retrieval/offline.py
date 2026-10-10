import json
import re
from pathlib import Path
from typing import Protocol
from deepresearch.models import RetrievedDocument
class SearchProvider(Protocol):
    def search(self, query: str) -> list[RetrievedDocument]: ...
class DocumentReader(Protocol):
    def read(self, document_id: str) -> RetrievedDocument: ...
class OfflineSearchProvider:
    def __init__(self, path):
        rows = json.loads(Path(path).read_text(encoding='utf-8'))
        self.documents = {r['document_id']:RetrievedDocument(**r) for r in rows}
        if len(self.documents) != len(rows): raise ValueError('Duplicate document ID')
    def search(self, query):
        terms=set(re.findall(r'\w+',query.lower()))
        scored=[]
        for doc in self.documents.values():
            score=len(terms & set(re.findall(r'\w+',doc.title.lower())))
            if score: scored.append((score, doc))
        return [d for _,d in sorted(scored,key=lambda x:(-x[0],x[1].document_id))]
    def read(self, document_id):
        return self.documents[document_id]
