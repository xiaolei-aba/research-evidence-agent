"""Read original-source snapshots without executing tutorial or header content."""
from hashlib import sha256
import json
from pathlib import Path
from deepresearch.retrieval.corpus import CorpusChunk


class OriginalCorpusReader:
    def __init__(self,root,catalog_path='data/raw_ai_v1/catalog.json',chunks_path='data/raw_ai_v1/chunks.json'):
        root=Path(root).resolve(); self.root=root; self.documents={}; self.chunks={}
        def local(name):
            path=(root/name).resolve()
            if not path.is_relative_to(root): raise ValueError('source_path_outside_project')
            return path
        for row in json.loads(local(catalog_path).read_text(encoding='utf-8')):
            raw=local(row['original_path']).read_bytes()
            text=local(row['text_path']).read_bytes().decode('utf-8')
            if sha256(raw).hexdigest()!=row['original_sha256']: raise ValueError('original_hash_mismatch')
            if sha256(text.encode()).hexdigest()!=row['text_sha256']: raise ValueError('extracted_text_hash_mismatch')
            if row['document_id'] in self.documents: raise ValueError('duplicate_original_document')
            self.documents[row['document_id']]=dict(row,text=text)
        for row in json.loads(local(chunks_path).read_text(encoding='utf-8')):
            doc=self.documents[row['document_id']]
            start,end=row['start'],row['end']
            if type(start) is not int or type(end) is not int or not 0<=start<end<=len(doc['text']):
                raise ValueError('original_chunk_span_invalid')
            text=doc['text'][start:end]
            if sha256(text.encode()).hexdigest()!=row['sha256']: raise ValueError('chunk_hash_mismatch')
            if row['chunk_id'] in self.chunks: raise ValueError('duplicate_original_chunk')
            chunk=CorpusChunk(doc['document_id'],doc['source_id'],doc['title'],doc['source_url'],doc['source_version'],
                doc['license'],doc['retrieved_at'],doc['text_sha256'],row['section_id'],row['chunk_id'],
                {'start':start,'end':end,'text':text},text,doc['text'],doc['representation'])
            chunk.validate(); self.chunks[row['chunk_id']]=chunk

    def read(self,chunk_id):
        return self.chunks[chunk_id].as_document()
