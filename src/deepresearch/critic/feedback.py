from deepresearch.models import CriticFeedback

def parse_feedback(parsed):
    if type(parsed.get('sufficient')) is not bool or not isinstance(parsed.get('reason'),str):
        raise ValueError('Invalid critic output')
    for key in ('missing_queries','conflicts'):
        if not isinstance(parsed.get(key),list) or not all(isinstance(x,str) for x in parsed[key]):
            raise ValueError('Invalid critic lists')
    return CriticFeedback(**parsed)
