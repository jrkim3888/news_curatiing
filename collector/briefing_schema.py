"""The provider's constrained output schema; evidence is still checked locally."""
from .core import CATEGORIES


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
    evidence = obj({"article_id": identity, "quote": text(12, 240)})
    link = obj({"text": text(2, 30), "article_id": identity})
    fact = obj({"text": text(15, 400), "evidence": array(evidence, 1, 3),
                "links": array(link, 0, 3)})
    issue = obj({"title": text(5, 140), "category": category,
                 "facts": array(fact, 1, 3), "why_it_matters": text(15, 300)})
    overview = obj({"text": text(70, 300),
                    "issue_indexes": array({"type": "integer", "minimum": 0, "maximum": 4}, 1, 3)})
    summary = obj({"article_id": identity, "title": text(5, 140),
                   "summary": text(30, 600), "why_it_matters": text(15, 300),
                   "category": category, "evidence": array(text(12, 240), 1, 3)})
    return {"type": "json_schema", "json_schema": {
        "name": "daily_brief_v4", "strict": True,
        "schema": obj({"issues": array(issue, 1, 5), "overview": array(overview, 1, 3),
                       "headline": text(6, 60), "takeaway": text(20, 120),
                       "article_summaries": array(summary, 1, 16)}),
    }}
