import re
from deepresearch.utils.redaction import redact, environment_secrets
from dataclasses import asdict
from time import perf_counter
from deepresearch.models import ToolResult, TraceEvent
class BudgetExceeded(RuntimeError): pass
class ToolExecutor:
    def __init__(self, search, reader, trace, max_calls=12, cache=True, redactor=None):
        self.search=search; self.reader=reader; self.trace=trace
        self.redact=redactor or (lambda value: redact(value,environment_secrets()))
        self.max_calls=max_calls; self.cache=cache; self.entries={}
        self.hits=0; self.lookups=0
    def call(self, request):
        start=perf_counter()
        self.lookups+=1
        if (request.tool not in ('search','read') or not isinstance(request.argument,str)
                or not request.argument.strip() or len(request.argument)>2048
                or (request.tool=='read' and not re.fullmatch(r'[A-Za-z0-9_.:-]+',request.argument))):
            result=ToolResult(request.request_id,'error',error='invalid_or_disallowed_tool_request')
            self.trace.events.append(TraceEvent('tool','rejected',0,self.redact({'request':request.__dict__}),result.error))
            return result
        key=(request.tool,request.argument)
        if self.cache and key in self.entries:
            self.hits+=1
            result=ToolResult(request.request_id,'ok',self.entries[key])
            self.trace.events.append(TraceEvent('tool','cache_hit',0,self.redact({'request':request.__dict__,'result':asdict(result)})))
            return result
        if self.trace.tool_calls>=self.max_calls:
            self.trace.events.append(TraceEvent('tool','budget_exceeded',0,self.redact({'request':request.__dict__}),'Tool call limit reached'))
            raise BudgetExceeded('Tool call limit reached')
        self.trace.tool_calls+=1
        try:
            if request.tool=='search': docs=self.search.search(request.argument)
            elif request.tool=='read': docs=[self.reader.read(request.argument)]
            else: raise ValueError('Unknown tool')
            result=ToolResult(request.request_id,'ok',docs)
            if self.cache: self.entries[key]=docs
        except Exception as exc: result=ToolResult(request.request_id,'error',error=self.redact(str(exc)))
        self.trace.events.append(TraceEvent('tool',result.status,(perf_counter()-start)*1000,
            self.redact({'request':request.__dict__,'result':{'request_id':result.request_id,'document_ids':[d.document_id for d in result.documents],
             'chunk_ids':[d.chunk_id for d in result.documents]}}),result.error))
        return result
