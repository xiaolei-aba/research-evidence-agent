"""Structural provenance checks plus an explicit optional semantic review boundary."""
from hashlib import sha256
from typing import Protocol


class SemanticSupportJudge(Protocol):
    def supports(self,claim: str,evidence: str) -> bool: ...


class CitationVerifier:
    def __init__(self,enabled=True,semantic_judge=None,reader=None):
        self.enabled=enabled; self.semantic_judge=semantic_judge; self.reader=reader

    def verify(self,citations,store):
        findings=[]
        for citation in citations:
            item=store.items.get(citation.evidence_id)
            finding={'citation_id':citation.citation_id,'evidence_id':citation.evidence_id,
                     'structural_valid':False,'support':None,'method':'not_evaluated','error':''}
            if item is None:
                finding['error']='unknown_evidence_id'
            elif not isinstance(citation.claim,str) or not citation.claim.strip():
                finding['error']='empty_claim'
            else:
                try:
                    if item.chunk_id:
                        if self.reader is not None:
                            original=self.reader.read(item.chunk_id)
                            for name in ('document_id','source','source_id','source_version','section_id',
                                         'span_start','span_end','content','document_sha256','chunk_sha256'):
                                if getattr(item,name)!=getattr(original,name):
                                    raise ValueError('source_registry_mismatch')
                        if not (item.source_id and item.section_id and item.source_version and item.source):
                            raise ValueError('missing_source_locator')
                        if sha256(item.source_text.encode()).hexdigest()!=item.document_sha256:
                            raise ValueError('source_hash_mismatch')
                        if not 0<=item.span_start<item.span_end<=len(item.source_text):
                            raise ValueError('span_bounds_mismatch')
                        if item.source_text[item.span_start:item.span_end]!=item.content:
                            raise ValueError('source_span_mismatch')
                        if sha256(item.content.encode()).hexdigest()!=item.chunk_sha256:
                            raise ValueError('chunk_hash_mismatch')
                    finding['structural_valid']=True
                    if not self.enabled:
                        finding['method']='semantic_verifier_disabled'
                    elif self.semantic_judge is not None:
                        label=self.semantic_judge.supports(citation.claim,item.content)
                        if type(label) is not bool: raise ValueError('invalid_support_label')
                        finding['support']=label; finding['method']='external_semantic_review'
                    else:
                        finding['method']='provenance_only_semantics_unknown'
                    finding['locator']={'source':item.source,'source_id':item.source_id,
                        'document_id':item.document_id,'chunk_id':item.chunk_id,'section_id':item.section_id,
                        'start':item.span_start,'end':item.span_end,'source_version':item.source_version}
                except (ValueError,KeyError) as exc:
                    finding['structural_valid']=False; finding['error']=str(exc)
            findings.append(finding)
        return {'findings':findings,'structural_valid':all(f['structural_valid'] for f in findings),
                'semantic_support_complete':bool(findings) and all(f['support'] is not None for f in findings)}
