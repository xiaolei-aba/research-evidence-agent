from datetime import datetime, timezone
from hashlib import sha256
from deepresearch.models import EvidenceItem
class EvidenceStore:
    def __init__(self, deduplicate=True):
        self.deduplicate=deduplicate
        self.items={}
        self.keys={}
    def add(self, doc, task_id):
        key=sha256((doc.document_id+'\0'+doc.source+'\0'+doc.source_version+'\0'+doc.chunk_id+'\0'+doc.content).encode()).hexdigest()
        if self.deduplicate and key in self.keys:
            item=self.items[self.keys[key]]
            if task_id not in item.task_ids: item.task_ids.append(task_id)
            return item
        eid='ev-'+key[:16]+'-'+str(len(self.items))
        item=EvidenceItem(eid,doc.document_id,doc.source,doc.content,[task_id],datetime.now(timezone.utc).isoformat(),'full-document',doc.facts)
        for name in ('source_id','source_version','license','section_id','chunk_id','document_sha256',
                     'chunk_sha256','span_start','span_end','source_text'):
            setattr(item,name,getattr(doc,name))
        if doc.chunk_id: item.locator=f'{doc.section_id}:{doc.span_start}:{doc.span_end}'
        self.items[eid]=item
        self.keys[key]=eid
        return item
    def validate(self, citation):
        if citation.evidence_id not in self.items: raise ValueError('Unknown evidence ID')
        if citation.claim not in self.items[citation.evidence_id].content: raise ValueError('Claim is not an exact evidence excerpt')
    def conflicts(self):
        seen={}
        for e in self.items.values():
            for k,v in e.facts.items(): seen.setdefault(k,set()).add(str(v))
        return sorted(k for k,v in seen.items() if len(v)>1)
