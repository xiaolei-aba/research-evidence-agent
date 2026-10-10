"""Versioned, source-located corpus records. Only local files are read."""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from deepresearch.models import JsonModel, RetrievedDocument


@dataclass
class CorpusChunk(JsonModel):
    document_id: str
    source_id: str
    title: str
    source_url: str
    source_version: str
    license: str
    retrieved_at: str
    sha256: str
    section_id: str
    chunk_id: str
    evidence_span: dict
    text: str
    document_text: str
    representation: str

    def validate(self):
        for name in ('document_id','source_id','title','source_url','source_version','license',
                     'retrieved_at','section_id','chunk_id','representation'):
            if not isinstance(getattr(self,name),str) or not getattr(self,name).strip():
                raise ValueError('missing_corpus_metadata: '+name)
        if sha256(self.document_text.encode()).hexdigest()!=self.sha256:
            raise ValueError('document_hash_mismatch')
        if set(self.evidence_span)!={'start','end','text'}:
            raise ValueError('invalid_span_fields')
        start,end=self.evidence_span['start'],self.evidence_span['end']
        if type(start) is not int or type(end) is not int or not 0<=start<end<=len(self.document_text):
            raise ValueError('invalid_span_bounds')
        if self.document_text[start:end]!=self.text or self.evidence_span['text']!=self.text:
            raise ValueError('evidence_span_mismatch')

    def as_document(self):
        self.validate()
        return RetrievedDocument(self.document_id,self.source_url,self.title,self.text,{},
            source_id=self.source_id,source_version=self.source_version,license=self.license,
            section_id=self.section_id,chunk_id=self.chunk_id,document_sha256=self.sha256,
            chunk_sha256=sha256(self.text.encode()).hexdigest(),span_start=self.evidence_span['start'],
            span_end=self.evidence_span['end'],source_text=self.document_text)


class CorpusReader:
    def __init__(self,path):
        rows=json.loads(Path(path).read_text(encoding='utf-8'))
        self.chunks={}
        parents={}
        for row in rows:
            chunk=CorpusChunk(**row); chunk.validate()
            if chunk.chunk_id in self.chunks: raise ValueError('duplicate_chunk_id')
            parent=(chunk.sha256,chunk.source_url,chunk.source_version,chunk.document_text,
                    chunk.source_id,chunk.title,chunk.license,chunk.retrieved_at,chunk.representation)
            if chunk.document_id in parents and parents[chunk.document_id]!=parent:
                raise ValueError('inconsistent_document_version')
            parents[chunk.document_id]=parent; self.chunks[chunk.chunk_id]=chunk

    def read(self,chunk_id):
        return self.chunks[chunk_id].as_document()
