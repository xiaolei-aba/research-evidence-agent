"""Deterministic BM25 baseline, without a vector model or external dependencies."""
from collections import Counter
import math
import re
import sys
from typing import Protocol


def tokenize(text):
    # Share immutable vocabulary strings across sentence counters; token values/scores are unchanged.
    return [sys.intern(token) for token in re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]',text.lower())]


class EmbeddingProvider(Protocol):
    def embed(self,texts: list[str]) -> list[list[float]]: ...


class BM25SearchProvider:
    backend_name='bm25-v1'
    def __init__(self,reader,top_k=5,k1=1.2,b=.75):
        if type(top_k) is not int or top_k<1 or not math.isfinite(k1) or k1<=0 or not 0<=b<=1:
            raise ValueError('invalid_bm25_config')
        self.reader=reader; self.top_k=top_k; self.k1=k1; self.b=b
        self.terms={key:Counter(tokenize(chunk.title+' '+chunk.text)) for key,chunk in reader.chunks.items()}
        self.lengths={key:sum(terms.values()) for key,terms in self.terms.items()}
        self.average=sum(self.lengths.values())/len(self.lengths) if self.lengths else 1
        self.df=Counter(term for terms in self.terms.values() for term in terms)

    def search(self,query):
        results=[]; count=len(self.terms)
        for key,terms in self.terms.items():
            score=0.0
            for term in set(tokenize(query)):
                freq=terms[term]
                if freq:
                    idf=math.log(1+(count-self.df[term]+.5)/(self.df[term]+.5))
                    norm=freq+self.k1*(1-self.b+self.b*self.lengths[key]/self.average)
                    score+=idf*freq*(self.k1+1)/norm
            if score>0: results.append((score,key))
        docs=[]
        for score,key in sorted(results,key=lambda item:(-item[0],item[1]))[:self.top_k]:
            doc=self.reader.read(key); doc.retrieval_score=score; docs.append(doc)
        return docs
