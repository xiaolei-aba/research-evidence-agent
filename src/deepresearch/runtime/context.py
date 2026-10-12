"""Whole-evidence request selection; source memory and model protocol stay intact.

Only the request's evidence list changes. Native counting includes the entire
request as rendered by the original provider, including candidate citations and
metadata. No text truncation, ID rewriting, model summary, or Gold-dependent rank.
"""
from copy import deepcopy
from hashlib import sha256
import json
import re


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode('utf-8')).hexdigest()


def _words(value):
    return set(re.findall(r'\w+', value.lower())) if isinstance(value, str) else set()


def _source_identity(item):
    """Missing provenance never licenses deduplication on text alone."""
    required = ('document_id', 'source', 'source_version', 'content')
    if any(not isinstance(item.get(k), str) or not item[k] for k in required):
        return None
    if not (item.get('chunk_id') or item.get('locator')):
        return None
    return tuple(item.get(k, '') for k in (
        'document_id', 'source', 'source_version', 'section_id', 'chunk_id',
        'locator', 'document_sha256', 'chunk_sha256', 'content'))


def validate_selected_citations(result, prepared_payload):
    """Model citation scope is this request, not the entire EvidenceStore."""
    allowed = {e['evidence_id'] for e in prepared_payload.get('evidence', [])
               if isinstance(e, dict) and isinstance(e.get('evidence_id'), str)}
    citations = result.get('citations', [])
    if not isinstance(citations, list):
        raise ValueError('context_citation_list_invalid')
    for citation in citations:
        if not isinstance(citation, dict) or citation.get('evidence_id') not in allowed:
            raise ValueError('context_citation_outside_supplied_evidence')
    return result


class ContextManager:
    def __init__(self, enabled=True, deduplicate=True, rank=True):
        if any(type(v) is not bool for v in (enabled, deduplicate, rank)):
            raise ValueError('invalid_context_switch')
        self.enabled = enabled
        self.deduplicate = deduplicate
        self.rank = rank

    def prepare(self, operation, payload, counter, max_input_tokens):
        if not isinstance(payload, dict):
            raise ValueError('context_payload_must_be_mapping')
        if type(max_input_tokens) is not int or max_input_tokens < 1:
            raise ValueError('invalid_context_token_limit')
        count_calls = 0

        def count(value):
            nonlocal count_calls
            count_calls += 1
            tokens = counter.count_request(operation, value)
            if type(tokens) is not int or tokens < 0:
                raise ValueError('invalid_native_input_token_count')
            return tokens

        before = count(payload)
        evidence = payload.get('evidence')
        units = evidence if isinstance(evidence, list) else []
        ids = [e.get('evidence_id') for e in units if isinstance(e, dict)]
        decision = {
            'operation': operation, 'enabled': self.enabled,
            'policy': 'whole-source-units-query-overlap-stable-order-v1',
            'counter_name': getattr(counter, 'name', type(counter).__name__),
            'counter_is_exact': bool(getattr(counter, 'is_exact', False)),
            'max_input_tokens': max_input_tokens, 'before_tokens': before,
            'after_tokens': before, 'count_calls': count_calls,
            'input_payload_sha256': _digest(payload),
            'output_payload_sha256': _digest(payload),
            'candidate_ids': ids, 'selected_ids': ids, 'dropped': [],
            'kept': [], 'pinned_ids': [], 'missing_pin_ids': [],
            'memory_mutated': False, 'text_truncated': False,
            'semantic_support': 'unknown; selection is not support verification',
            'semantic_impact': 'none; input unchanged',
        }
        if not self.enabled or not isinstance(evidence, list) or not units:
            decision['status'] = 'unchanged'
            decision['reason'] = ('disabled' if not self.enabled else 'no_evidence_units')
            return payload, decision
        if any(not isinstance(e, dict) or not isinstance(e.get('evidence_id'), str)
               or not e['evidence_id'] or not isinstance(e.get('content'), str) for e in units):
            raise ValueError('context_evidence_unit_invalid')
        if len(ids) != len(set(ids)):
            raise ValueError('context_duplicate_evidence_id')
        pinned = set()
        if operation in {'critic', 'critique'} and payload.get('answer') is not None:
            answer = payload['answer']
            if not isinstance(answer, dict) or not isinstance(answer.get('citations'), list):
                raise ValueError('context_candidate_citations_invalid')
            for citation in answer['citations']:
                if not isinstance(citation, dict) or not isinstance(citation.get('evidence_id'), str):
                    raise ValueError('context_candidate_citation_invalid')
                pinned.add(citation['evidence_id'])
        decision['pinned_ids'] = sorted(pinned)
        decision['missing_pin_ids'] = sorted(pinned.difference(ids))
        if decision['missing_pin_ids']:
            decision['status'] = 'invalid_pin_reference'
            decision['reason'] = 'candidate_references_unavailable_evidence'
            return payload, decision
        if before <= max_input_tokens:
            decision['status'] = 'unchanged'
            decision['reason'] = 'full_request_fits'
            decision['kept'] = [{'evidence_id': e['evidence_id'], 'index': i,
                                 'reason': 'full_request_fits'} for i, e in enumerate(units)]
            return payload, decision

        keep = set(range(len(units)))
        if self.deduplicate:
            groups = {}
            for i, item in enumerate(units):
                identity = _source_identity(item)
                if identity is not None:
                    groups.setdefault(identity, []).append(i)
            for indices in groups.values():
                pinned_indices = [i for i in indices if units[i]['evidence_id'] in pinned]
                representatives = set(pinned_indices or [indices[0]])
                for i in indices:
                    if i not in representatives:
                        keep.remove(i)
                        decision['dropped'].append({
                            'evidence_id': units[i]['evidence_id'], 'index': i,
                            'reason': 'duplicate_same_source_unit',
                            'representative_ids': [units[j]['evidence_id'] for j in sorted(representatives)],
                        })
        changed = deepcopy(payload)

        def assemble():
            changed['evidence'] = [deepcopy(units[i]) for i in range(len(units)) if i in keep]
            return changed

        after = count(assemble())
        query = _words(payload.get('query', ''))
        overlap = {i: len(query.intersection(_words(units[i]['content']))) for i in keep}
        removable = [i for i in keep if units[i]['evidence_id'] not in pinned]
        # Ranking determines eviction only. Surviving evidence retains input order.
        removable.sort(key=lambda i: (overlap[i] if self.rank else 0, -i))
        for i in removable:
            if after <= max_input_tokens:
                break
            keep.remove(i)
            decision['dropped'].append({'evidence_id': units[i]['evidence_id'], 'index': i,
                                        'reason': 'whole_request_token_budget',
                                        'query_term_overlap': overlap[i]})
            after = count(assemble())
        selected = [units[i]['evidence_id'] for i in range(len(units)) if i in keep]
        decision.update(after_tokens=after, count_calls=count_calls, selected_ids=selected,
                        output_payload_sha256=_digest(changed),
                        semantic_impact='input evidence changed; answer impact unknown')
        decision['kept'] = [{'evidence_id': units[i]['evidence_id'], 'index': i,
                             'reason': 'candidate_citation_pinned' if units[i]['evidence_id'] in pinned
                             else 'remaining_whole_unit_within_budget',
                             'query_term_overlap': overlap.get(i, 0)}
                            for i in range(len(units)) if i in keep]
        if after > max_input_tokens:
            decision['status'] = 'budget_unresolved'
            decision['reason'] = 'pinned_units_or_non_evidence_metadata_exceed_budget'
        elif not selected:
            decision['status'] = 'empty_after_selection'
            decision['reason'] = 'no_whole_evidence_unit_fits'
        else:
            decision['status'] = 'selected'
            decision['reason'] = 'whole_source_units_selected'
        return changed, decision
