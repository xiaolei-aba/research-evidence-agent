from deepresearch.utils.redaction import redact, environment_secrets
from deepresearch.llm.validation import validate_output
from deepresearch.agent.planner import parse_plan
from deepresearch.critic.feedback import parse_feedback
import json
from dataclasses import asdict
from time import perf_counter
from deepresearch.models import *
from deepresearch.memory.store import EvidenceStore
from deepresearch.tools.executor import ToolExecutor
class Agent:
    def __init__(self, provider, search, reader, max_replans=1, max_tool_calls=12, cache=True, deduplicate=True, critic=True, replan=True):
        if max_replans<0 or max_tool_calls<0: raise ValueError('Negative budget')
        self.provider=provider; self.search=search; self.reader=reader
        self.max_replans=max_replans; self.max_tool_calls=max_tool_calls
        self.cache=cache; self.deduplicate=deduplicate
        self.critic=critic; self.replan=replan
    def run(self, query):
        start=perf_counter(); trace=AgentTrace(query.query_id)
        store=EvidenceStore(self.deduplicate)
        tools=ToolExecutor(self.search,self.reader,trace,self.max_tool_calls,self.cache,
                           redactor=getattr(self.provider, "redact", None))
        plans=[]; feedbacks=[]
        def safe(value):
            return redact(getattr(self.provider, "redact", lambda x: x)(value), environment_secrets())
        def llm(op,payload):
            t=perf_counter()
            trace.model_calls += 1
            logical_call_id=uid()
            attempts = getattr(self.provider, 'attempts', None)
            cursor = len(attempts) if attempts is not None else 0
            try:
                raw=self.provider.complete(op,payload)
                result=validate_output(op,raw)
                trace.events.append(TraceEvent('llm:'+op,'ok',(perf_counter()-t)*1000,
                    safe({'logical_call_id':logical_call_id,'input':payload,'output':result})))
                return result
            except Exception as exc:
                trace.events.append(TraceEvent('llm:'+op,'error',(perf_counter()-t)*1000,
                    safe({'logical_call_id':logical_call_id,'input':payload}),safe(str(exc))))
                raise
            finally:
                if attempts is not None:
                    for event in attempts[cursor:]:
                        trace.events.append(TraceEvent('llm_attempt',event['status'],event['elapsed_ms'],safe(dict(event,logical_call_id=logical_call_id)),safe(event.get('error',''))))
                        if event.get('sent'):
                            trace.http_attempts += 1
                            usage=event.get('usage')
                            if usage is None: trace.usage_unreported_attempts += 1
                            else:
                                trace.prompt_tokens_reported += usage['prompt_tokens']
                                trace.completion_tokens_reported += usage['completion_tokens']
                                trace.token_usage_reported += usage['total_tokens']
        trace.events.append(TraceEvent('agent','started',0,{'configuration':{
            'cache':self.cache,'deduplicate':self.deduplicate,'critic':self.critic,'replan':self.replan,
            'max_replans':self.max_replans,'max_tool_calls':self.max_tool_calls}}))
        answer=None
        try:
            missing=[]
            for round_id in range((self.max_replans if self.replan else 0)+1):
                parsed=llm('plan' if round_id==0 else 'replan',{'query':query.text,'missing_queries':missing,'evidence':[asdict(e) for e in store.items.values()]})
                plan=parse_plan(parsed,query.query_id,round_id)
                plans.append(plan); missing=[]
                for task in plan.tasks:
                    task.status='running'; trace.events.append(TraceEvent('task','running',0,safe(asdict(task))))
                    result=tools.call(ToolRequest(task.task_id,'search',task.query))
                    found=False
                    for hit in result.documents:
                        read=tools.call(ToolRequest(task.task_id,'read',hit.chunk_id or hit.document_id))
                        if read.status=='ok':
                            for doc in read.documents:
                                evidence=store.add(doc,task.task_id); found=True
                                trace.events.append(TraceEvent('evidence','stored',0,safe({
                                    'request_id':read.request_id,'evidence':asdict(evidence)})))
                        else: task.error=read.error
                    if not found: missing.append(task.query)
                    task.status='completed' if found else 'failed'
                    if result.status=='error': task.error=result.error
                    trace.events.append(TraceEvent('task',task.status,0,safe(asdict(task)),safe(task.error)))
                critic_input={'query':query.text,'evidence':[asdict(e) for e in store.items.values()],
                              'missing_queries':missing,'conflicts':store.conflicts()}
                if self.critic:
                    feedback=parse_feedback(llm('critic',critic_input))
                else:
                    feedback=CriticFeedback(bool(store.items) and not missing and not store.conflicts(),
                                            'Critic disabled; deterministic coverage guard only',missing,store.conflicts())
                    trace.events.append(TraceEvent('critic','disabled',0,safe(asdict(feedback))))
                feedbacks.append(feedback)
                # Controller veto prevents a provider from ignoring known missing evidence or conflicts.
                if feedback.sufficient and store.items and not missing and not store.conflicts(): break
                missing=feedback.missing_queries or missing or [query.text]
            else:
                answer=FinalAnswer(query.query_id,'Insufficient evidence to answer the query within the configured research budget.',[],'insufficient_evidence','Replan limit reached or unresolved conflict')
            if answer is None:
                parsed=llm('answer',{'query':query.text,'evidence':[asdict(e) for e in store.items.values()]})
                if not isinstance(parsed.get('text'),str) or not parsed['text'].strip(): raise ValueError('Empty answer')
                citations=[Citation(**c) for c in parsed['citations']]
                if not citations: raise ValueError('Missing citations')
                for c in citations: store.validate(c)
                if parsed['text'] != '\n'.join(c.claim for c in citations): raise ValueError('B0 answer must consist of validated citation excerpts')
                answer=FinalAnswer(query.query_id,parsed['text'],citations,'completed')
        except Exception as exc:
            for plan in plans:
                for task in plan.tasks:
                    if task.status=='running':
                        task.status='failed'; task.error=safe(str(exc))
                        trace.events.append(TraceEvent('task','failed',0,safe(asdict(task)),task.error))
            answer=FinalAnswer(query.query_id,'',[],'failed',safe(str(exc)))
            trace.events.append(TraceEvent('agent','error',0,{},safe(str(exc))))
        trace.token_usage_complete=bool(trace.http_attempts) and trace.usage_unreported_attempts==0
        trace.token_usage=trace.token_usage_reported if trace.token_usage_complete else None
        trace.latency_ms=(perf_counter()-start)*1000
        trace.events.append(TraceEvent('agent',answer.status,trace.latency_ms,safe({'answer_id':answer.answer_id,'citations':[asdict(c) for c in answer.citations],
            'evidence_ids':list(store.items),'error':answer.error})))
        return safe({'answer':asdict(answer),'trace':asdict(trace),'plans':[asdict(x) for x in plans],
            'feedback':[asdict(x) for x in feedbacks],'evidence':[asdict(x) for x in store.items.values()],
            'cache':{'hits':tools.hits,'lookups':tools.lookups}})
