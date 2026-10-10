"""SQLite-backed source reader and the unchanged existing BM25 equation, bounded in-memory cache."""
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
import json
import math
import sqlite3
from deepresearch.retrieval.corpus import CorpusChunk
from deepresearch.retrieval.bm25 import tokenize


class LazyChunks(Mapping):
    def __init__(self,reader):self.reader=reader
    def __getitem__(self,key):return self.reader.chunk(key)
    def __iter__(self):return (row[0] for row in self.reader.db.execute('SELECT id FROM chunks ORDER BY rowid'))
    def __len__(self):return self.reader.meta['count']


class LowMemoryReader:
    def __init__(self,path):
        self.db=sqlite3.connect('file:'+Path(path).resolve().as_posix()+'?mode=ro',uri=True)
        self.meta={k:json.loads(v) for k,v in self.db.execute('SELECT key,value FROM meta')}
        self.chunks=LazyChunks(self)
    @lru_cache(maxsize=32)
    def chunk(self,key):
        row=self.db.execute('SELECT c.doc,d.title,d.full,d.sha,c.locator,c.start,c.end,c.text FROM chunks c JOIN docs d ON c.doc=d.id WHERE c.id=?',(key,)).fetchone()
        if row is None:raise KeyError(key)
        doc,title,full,digest,locator,start,end,text=row
        result=CorpusChunk(doc,doc,title,'https://huggingface.co/datasets/allenai/scifact',self.meta['source_sha256'],
          'ODC-BY-1.0 abstract corpus','locked public dataset release',digest,locator,key,
          {'start':start,'end':end,'text':text},text,full,'official sentence; no annotations')
        result.validate();return result
    def read(self,key):return self.chunk(key).as_document()


class LowMemoryBM25:
    backend_name='bm25-v1-sqlite-exact-equation'
    def __init__(self,reader,top_k=5,k1=1.2,b=.75):self.reader=reader;self.top_k=top_k;self.k1=k1;self.b=b
    def search(self,query):
        scores={};n=self.reader.meta['count'];average=self.reader.meta['average']
        for term in set(tokenize(query)):
            found=self.reader.db.execute('SELECT df FROM terms WHERE term=?',(term,)).fetchone()
            if found is None:continue
            idf=math.log(1+(n-found[0]+.5)/(found[0]+.5))
            for cid,freq,length in self.reader.db.execute('SELECT p.chunk,p.tf,c.length FROM postings p JOIN chunks c ON p.chunk=c.id WHERE term=?',(term,)):
                norm=freq+self.k1*(1-self.b+self.b*length/average)
                scores[cid]=scores.get(cid,0)+idf*freq*(self.k1+1)/norm
        result=[]
        for cid,score in sorted(scores.items(),key=lambda item:(-item[1],item[0]))[:self.top_k]:
            doc=self.reader.read(cid);doc.retrieval_score=score;result.append(doc)
        return result
