"""The provider's constrained output schema; evidence is still checked locally."""
from .core import CATEGORIES

MAX_GENERATION_INPUTS = 8


def text(minimum, maximum):
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def array(items, minimum, maximum):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


def obj(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def response_format(inputs):
    identity = {"type": "string"}
    if inputs:
        identity["enum"] = [item["article_id"] for item in inputs]
    category = {"type": "string", "enum": list(CATEGORIES)}
    evidence = obj({"article_id": identity, "quote": text(12, 120)})
    link = obj({"text": text(2, 30), "article_id": identity})
    fact = obj({"text": text(15, 200), "evidence": array(evidence, 1, 2),
                "links": array(link, 0, 2)})
    issue = obj({"title": text(5, 100), "category": category,
                 "facts": array(fact, 1, 2), "why_it_matters": text(15, 120)})
    # Even three sentences plus joining spaces/punctuation fit the 300-character paragraph.
    # Internal periods may occur in model versions; sentence-ending punctuation occurs once.
    sentence = {**text(35, 98), "pattern": r"^(?:[^.!?。！？\n…]|\.[A-Za-z0-9])+(?:다|요|죠|까)[.!?]$"}
    overview = obj({"sentences": array(sentence, 2, 3),
                    "issue_indexes": array({"type": "integer", "minimum": 0, "maximum": 4}, 1, 3)})
    summary_ids = [i['article_id'] for i in inputs if i.get('needs_summary', True)]
    summary_identity = {"type": "string", "enum": summary_ids} if summary_ids else identity
    summary = obj({"article_id": summary_identity, "title": text(5, 100),
                   "summary": text(30, 220), "why_it_matters": text(15, 100),
                   "category": category, "evidence": array(text(12, 120), 1, 1)})
    return {"type": "json_schema", "json_schema": {
        "name": "daily_brief_v7", "strict": True,
        "schema": obj({"issues": array(issue, 1, 5), "overview": array(overview, 1, 3),
                       "headline": {**text(6, 60), "pattern": r"^[^·…|\n]+$"}, "takeaway": text(20, 120),
                       "article_summaries": array(summary, 0, min(MAX_GENERATION_INPUTS, len(summary_ids)))}),
    }}


def coverage_format(inputs, recent_coverage):
    decision = obj({"article_id": {"type": "string", "enum": [item['article_id'] for item in inputs]},
                    "previous_issue_id": {"type": ["string", "null"],
                                          "enum": [None, *(issue['id'] for issue in recent_coverage)]}})
    return {"type": "json_schema", "json_schema": {
        "name": "briefing_coverage_v2", "strict": True,
        "schema": obj({"coverage": array(decision, len(inputs), len(inputs)),
                       "selected_article_ids": array(decision['properties']['article_id'], 0,
                                                     min(MAX_GENERATION_INPUTS, len(inputs)))})}}
