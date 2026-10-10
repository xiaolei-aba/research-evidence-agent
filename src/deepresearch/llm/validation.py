"""Operation contracts shared by remote providers and the controller."""
import json

SCHEMAS = {
    "plan": {"type": "object", "properties": {"queries": {"type": "array", "minItems": 1,
             "items": {"type": "string", "minLength": 1}}}, "required": ["queries"], "additionalProperties": False},
    "critic": {"type": "object", "properties": {
        "sufficient": {"type": "boolean"}, "reason": {"type": "string"},
        "missing_queries": {"type": "array", "items": {"type": "string"}},
        "conflicts": {"type": "array", "items": {"type": "string"}}},
        "required": ["sufficient", "reason", "missing_queries", "conflicts"], "additionalProperties": False},
    "answer": {"type": "object", "properties": {"text": {"type": "string", "minLength": 1},
        "citations": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
        "evidence_id": {"type": "string", "minLength": 1}, "claim": {"type": "string", "minLength": 1}},
        "required": ["evidence_id", "claim"], "additionalProperties": False}}},
        "required": ["text", "citations"], "additionalProperties": False},
}
SCHEMAS["replan"] = SCHEMAS["plan"]


class OutputValidationError(ValueError):
    pass


def strict_json(text):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise OutputValidationError("duplicate_json_key")
            result[k] = v
        return result

    def constant(_):
        raise OutputValidationError("non_finite_json_number")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError) as exc:
        raise OutputValidationError("invalid_json") from None


def validate_output(operation, text, *, allow_uncited_abstention=False):
    if operation not in SCHEMAS:
        raise OutputValidationError("unknown_operation")
    value = strict_json(text)

    def check(schema, item):
        kind = schema["type"]
        valid = {"object": type(item) is dict, "array": type(item) is list,
                 "string": type(item) is str, "boolean": type(item) is bool}[kind]
        if not valid:
            raise OutputValidationError("invalid_field_type")
        if kind == "object":
            if set(item) != set(schema["required"]):
                raise OutputValidationError("missing_or_extra_field")
            for key, child in schema["properties"].items():
                check(child, item[key])
        elif kind == "array":
            if len(item) < schema.get("minItems", 0):
                raise OutputValidationError("empty_list")
            for child in item:
                check(schema["items"], child)
        elif kind == "string" and schema.get("minLength") and not item.strip():
            raise OutputValidationError("empty_string")

    schema=SCHEMAS[operation]
    if (allow_uncited_abstention and operation=='answer' and isinstance(value,dict)
            and value.get('text') in {'Unanswerable','NEI','ABSTAIN'} and value.get('citations')==[]):
        from copy import deepcopy
        schema=deepcopy(schema); schema['properties']['citations']['minItems']=0
    check(schema, value)
    return value
