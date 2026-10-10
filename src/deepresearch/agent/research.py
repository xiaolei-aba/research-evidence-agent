"""AI-knowledge workflow. Legacy Agent remains available for B0/B1 regression."""
from dataclasses import asdict
import json
from hashlib import sha256
import time

from deepresearch.agent.budget import ResearchLimits, RunBudget, ResearchBudgetExceeded
from deepresearch.agent.planner import parse_plan
from deepresearch.critic.feedback import parse_feedback
from deepresearch.critic.citations import CitationVerifier
from deepresearch.llm.validation import validate_output, SCHEMAS
from deepresearch.memory.selector import EvidenceSelector
from deepresearch.memory.store import EvidenceStore
from deepresearch.models import (AgentTrace, TraceEvent, UserQuery, SubTask, ResearchPlan,
    ToolRequest, Citation, FinalAnswer, CriticFeedback, uid)
from deepresearch.tools.executor import ToolExecutor
from deepresearch.utils.redaction import redact, environment_secrets


class MockByteCounter:
    """Engineering accounting only, explicitly not actual tokenizer usage."""
    is_exact=False
    name='mock_utf8_bytes_plus_512_overhead'
    def count_request(self,operation,payload):
        return len(json.dumps(payload,ensure_ascii=False).encode())+512


class ResearchEvidenceAgent:
    def __init__(self,provider,retriever,reader,*,limits=None,selector=None,verifier=None,
                 planner=True,retrieval=True,reading=True,memory=True,critic=True,replan=True,
                 cache=False,deduplicate=False,token_counter=None,clock=time.monotonic,versions=None,
                 output_validator=validate_output,trace_factory=AgentTrace):
        self.provider=provider; self.retriever=retriever; self.reader=reader
        self.limits=limits or ResearchLimits(); self.selector=selector or EvidenceSelector()
        self.verifier=verifier or CitationVerifier(reader=reader if hasattr(reader,'chunks') else None)
        self.flags={'planner':planner,'retrieval':retrieval,'reading':reading,'memory':memory,
                    'critic':critic,'replan':replan,'cache':cache,'deduplicate':deduplicate}
        if any(type(flag) is not bool for flag in self.flags.values()): raise ValueError('invalid_module_switch')
        self.counter=token_counter or MockByteCounter()
        self.output_validator=output_validator
        self.trace_factory=trace_factory
        if not getattr(provider,'is_mock',False):
            if not getattr(self.counter,'is_exact',False): raise ValueError('real_provider_requires_exact_request_token_counter')
            config=getattr(provider,'config',None)
            if config is None or config.max_output_tokens>self.limits.max_output_tokens:
                raise ValueError('provider_output_cap_must_match_run_budget')
        self.clock=clock
        self.versions={'workflow':'research-evidence-v1','retriever':getattr(retriever,'backend_name','legacy'),
                       'token_counter':self.counter.name,'provider':type(provider).__name__,
                       'model_id':getattr(getattr(provider,'config',None),'model_id','mock-no-weights'),
                       'prompt_schema_sha256':sha256(json.dumps(SCHEMAS,sort_keys=True).encode()).hexdigest(),
                       **(versions or {})}

    def run(self,query):
        trace=self.trace_factory(query.query_id,versions=self.versions.copy())
        budget=RunBudget(self.limits,self.clock)
        safe=getattr(self.provider,'redact',lambda value:redact(value,environment_secrets()))
        store=EvidenceStore(self.flags['deduplicate'])
        tools=ToolExecutor(self.retriever,self.reader,trace,self.limits.max_tool_calls,self.flags['cache'],redactor=safe)
        plans=[]; feedbacks=[]; retrieval_ids=[]; selected_ids=[]
        def event(kind,status,payload,error='',elapsed=0):
            trace.events.append(TraceEvent(kind,status,elapsed,safe(payload),safe(error)))

        def call(operation,payload):
            attempts=getattr(self.provider,'attempts',None)
            cursor=len(attempts) if attempts is not None else 0
            retries=getattr(getattr(self.provider,'config',None),'max_retries',0)+1
            counted=self.counter.count_request(operation,payload)
            reservation={'operation':operation,'native_input_tokens':counted,
                'counter_is_exact':self.counter.is_exact,'attempts_reserved':retries,
                'output_cap_per_attempt':self.limits.max_output_tokens,
                'requested_reservation_tokens':(counted+self.limits.max_output_tokens)*retries,
                'prior_reserved_tokens':budget.reserved_tokens,'limits':asdict(self.limits)}
            event('token_budget','requested',reservation)
            try:
                budget.reserve(counted,retries)
            except Exception as exc:
                event('token_budget','rejected',dict(reservation,error_stage=operation),str(exc))
                raise
            event('token_budget','accepted',dict(reservation,reserved_tokens=budget.reserved_tokens))
            logical=uid(); start=self.clock(); trace.model_calls+=1
            try:
                raw=self.provider.complete(operation,safe(payload))
                result=self.output_validator(operation,raw)
                event('llm:'+operation,'ok',{'logical_call_id':logical,'input':payload,'output':result},elapsed=(self.clock()-start)*1000)
                return result
            except Exception as exc:
                event('llm:'+operation,'error',{'logical_call_id':logical,'input':payload},str(exc),(self.clock()-start)*1000)
                raise
            finally:
                if attempts is not None:
                    for attempt in attempts[cursor:]:
                        event('llm_attempt',attempt['status'],dict(attempt,logical_call_id=logical),attempt.get('error',''),attempt['elapsed_ms'])
                        if attempt.get('sent'):
                            trace.http_attempts+=1
                            usage=attempt.get('usage')
                            if usage is None: trace.usage_unreported_attempts+=1
                            else:
                                trace.prompt_tokens_reported+=usage['prompt_tokens']
                                trace.completion_tokens_reported+=usage['completion_tokens']
                                trace.token_usage_reported+=usage['total_tokens']
                budget.check()
                if trace.token_usage_reported>self.limits.max_total_tokens:
                    raise ResearchBudgetExceeded('reported_token_budget_exceeded')

        def tool(request):
            budget.check()
            result=tools.call(request)
            budget.check()
            return result

        def public_evidence():
            return [{'evidence_id':e.evidence_id,'document_id':e.document_id,'source':e.source,
                     'source_version':e.source_version,'section_id':e.section_id,'chunk_id':e.chunk_id,
                     'locator':e.locator,'content':e.content} for e in store.items.values()]

        answer=None; missing=[]; report={}
        event('agent','started',{'configuration':self.flags,'versions':self.versions,'limits':asdict(self.limits)})
        try:
            if not isinstance(query.text,str) or not query.text.strip() or len(query.text)>2048:
                raise ValueError('invalid_query')
            rounds=(self.limits.max_replans if self.flags['replan'] else 0)+1
            for round_id in range(rounds):
                budget.check()
                if self.flags['planner']:
                    plan=parse_plan(call('plan' if round_id==0 else 'replan',{
                        'query':query.text,'missing_queries':missing,'evidence':public_evidence()}),query.query_id,round_id)
                else:
                    plan=ResearchPlan(query.query_id,[SubTask(f'{round_id}:0',query.text)])
                if len(plan.tasks)>self.limits.max_subtasks: raise ResearchBudgetExceeded('subtask_budget_exceeded')
                plans.append(plan); missing=[]
                if not self.flags['memory']: store=EvidenceStore(self.flags['deduplicate'])
                for task in plan.tasks:
                    task.status='running'; event('task','running',asdict(task))
                    docs=[]
                    if self.flags['retrieval']:
                        result=tool(ToolRequest(task.task_id,'search',task.query))
                        retrieval_ids.extend(d.chunk_id or d.document_id for d in result.documents)
                        if result.status=='error': task.error=result.error
                        if self.flags['reading']:
                            for hit in result.documents:
                                read=tool(ToolRequest(task.task_id,'read',hit.chunk_id or hit.document_id))
                                if read.status=='ok':
                                    for document in read.documents:
                                        document.retrieval_score=hit.retrieval_score; docs.append(document)
                                else: task.error=read.error
                    chosen,selection=self.selector.select(docs)
                    event('selection','ok',dict(selection,task_id=task.task_id))
                    for doc in chosen:
                        item=store.add(doc,task.task_id)
                        selected_ids.append(doc.chunk_id or doc.document_id)
                        event('evidence','stored',asdict(item))
                    task.status='completed' if chosen else 'failed'
                    if not chosen: missing.append(task.query)
                    event('task',task.status,asdict(task),task.error)
                candidate=None
                if store.items:
                    parsed=call('answer',{'query':query.text,'evidence':public_evidence()})
                    citations=[Citation(**c) for c in parsed['citations']]
                    candidate=FinalAnswer(query.query_id,parsed['text'],citations,'completed')
                    report=self.verifier.verify(citations,store)
                    event('citation_verifier','checked',report)
                else:
                    report={'structural_valid':False,'findings':[],'semantic_support_complete':False}
                critic_input={'query':query.text,'evidence':public_evidence(),'missing_queries':missing,
                    'conflicts':store.conflicts(),'answer':{'text':candidate.text,'citations':[{'evidence_id':c.evidence_id,'claim':c.claim} for c in candidate.citations]} if candidate else None,
                    'citation_report':{'structural_valid':report['structural_valid'],
                        'findings':[{key:f[key] for key in ('evidence_id','structural_valid','support','error')} for f in report['findings']]}}
                if self.flags['critic']:
                    feedback=parse_feedback(call('critic',critic_input))
                else:
                    feedback=CriticFeedback(candidate is not None and not missing,'Model citation critic disabled',missing,store.conflicts())
                    event('critic','disabled',asdict(feedback))
                feedbacks.append(feedback)
                semantic_rejected=any(f.get('support') is False for f in report['findings'])
                if candidate and feedback.sufficient and not missing and not store.conflicts() and report['structural_valid'] and not semantic_rejected:
                    answer=candidate; break
                missing=feedback.missing_queries or missing or [query.text]
                event('replan','requested' if round_id+1<rounds else 'limit_reached',{'missing_queries':missing,'round':round_id})
            if answer is None:
                answer=FinalAnswer(query.query_id,'Insufficient verified evidence within the research budget.',[],
                                   'insufficient_evidence','Unresolved evidence or citation issues')
        except Exception as exc:
            for plan in plans:
                for task in plan.tasks:
                    if task.status=='running':
                        task.status='failed'; task.error=safe(str(exc)); event('task','failed',asdict(task),str(exc))
            answer=FinalAnswer(query.query_id,'',[],'failed',safe(str(exc)))
            event('agent','error',{},str(exc))
        trace.token_usage_complete=bool(trace.http_attempts) and trace.usage_unreported_attempts==0
        trace.token_usage=trace.token_usage_reported if trace.token_usage_complete else None
        trace.latency_ms=(self.clock()-budget.start)*1000
        trace.budget=budget.snapshot()
        event('agent',answer.status,{'answer':asdict(answer),'versions':self.versions,'budget':trace.budget},elapsed=trace.latency_ms)
        return safe({'answer':asdict(answer),'trace':asdict(trace),'plans':[asdict(p) for p in plans],
            'feedback':[asdict(f) for f in feedbacks],'evidence':[asdict(e) for e in store.items.values()],
            'citation_report':report,'retrieved_chunk_ids':retrieval_ids,'selected_chunk_ids':selected_ids,
            'cache':{'hits':tools.hits,'lookups':tools.lookups},'quality_status':'not_human_evaluated'})
