"""Cooperative run deadline and pre-call conservative token reservations."""
from dataclasses import dataclass
import time


class ResearchBudgetExceeded(RuntimeError): pass


@dataclass(frozen=True)
class ResearchLimits:
    max_subtasks: int = 4
    max_tool_calls: int = 12
    max_replans: int = 1
    max_model_calls: int = 10
    max_total_tokens: int = 24000
    max_input_tokens: int = 3072
    max_output_tokens: int = 512
    max_seconds: float = 120.0

    def __post_init__(self):
        import math
        for name,value in self.__dict__.items():
            if name=='max_seconds':
                if type(value) not in (int,float) or not math.isfinite(value) or value<=0:
                    raise ValueError('invalid_deadline')
            elif type(value) is not int or value < (0 if name in ('max_replans','max_tool_calls') else 1):
                raise ValueError('invalid_budget: '+name)


class RunBudget:
    def __init__(self,limits,clock=time.monotonic):
        self.limits=limits; self.clock=clock; self.start=clock()
        self.reserved_tokens=0; self.model_calls=0

    def check(self):
        if self.clock()-self.start>=self.limits.max_seconds:
            raise ResearchBudgetExceeded('run_deadline_exceeded')

    def reserve(self,input_tokens,attempts=1):
        self.check()
        if type(input_tokens) is not int or input_tokens<0 or input_tokens>self.limits.max_input_tokens:
            raise ResearchBudgetExceeded('input_token_budget_exceeded')
        if self.model_calls>=self.limits.max_model_calls:
            raise ResearchBudgetExceeded('model_call_budget_exceeded')
        amount=(input_tokens+self.limits.max_output_tokens)*attempts
        if self.reserved_tokens+amount>self.limits.max_total_tokens:
            raise ResearchBudgetExceeded('total_token_reservation_exceeded')
        self.reserved_tokens+=amount; self.model_calls+=1

    def snapshot(self):
        return {'limits':self.limits.__dict__,'reserved_tokens':self.reserved_tokens,
                'model_calls':self.model_calls,'deadline_kind':'cooperative; transport must bound blocking work'}
