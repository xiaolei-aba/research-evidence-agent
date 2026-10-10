"""Baseline lexical ordering/dedup and deterministic context cap, not a learned reranker."""
class EvidenceSelector:
    def __init__(self,enabled=False,deduplicate=False,max_chars=6000,max_items=8):
        if type(enabled) is not bool or type(deduplicate) is not bool: raise ValueError('invalid_selector_switch')
        if type(max_chars) is not int or max_chars<1 or type(max_items) is not int or max_items<1:
            raise ValueError('invalid_evidence_budget')
        self.enabled=enabled; self.deduplicate=deduplicate
        self.max_chars=max_chars; self.max_items=max_items

    def select(self,documents):
        candidates=sorted(documents,key=lambda d:(-d.retrieval_score,d.chunk_id or d.document_id)) if self.enabled else list(documents)
        selected=[]; rejected=[]; seen=set(); used=0
        for doc in candidates:
            key=(doc.source,doc.source_version,doc.chunk_id or doc.document_id,doc.content)
            if self.deduplicate and key in seen:
                rejected.append({'chunk_id':doc.chunk_id,'reason':'duplicate'}); continue
            seen.add(key)
            if len(selected)>=self.max_items or used+len(doc.content)>self.max_chars:
                rejected.append({'chunk_id':doc.chunk_id,'reason':'context_budget'}); continue
            selected.append(doc); used+=len(doc.content)
        return selected,{'selected_ids':[d.chunk_id or d.document_id for d in selected],
                         'rejected':rejected,'selected_chars':used,'ranking_enabled':self.enabled}
