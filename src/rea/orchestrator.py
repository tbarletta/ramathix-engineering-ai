from __future__ import annotations

from .domain import IssueWorkUnit, TechLeadPlan
from .models import ModelRouter, OllamaClient

TECH_LEAD_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "affected_areas": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "tests": {"type": "array", "items": {"type": "string"}},
        "cost_impact": {"type": "boolean"},
    },
    "required": ["summary", "affected_areas", "risks", "tests", "cost_impact"],
}


class TechLeadAgent:
    def __init__(self, router: ModelRouter, ollama: OllamaClient) -> None:
        self.router = router
        self.ollama = ollama

    def analyze(self, issue: IssueWorkUnit) -> TechLeadPlan:
        target = self.router.resolve("tech_lead")
        system = (
            "You are the Ramathix Engineering AI Tech Lead. Analyze requirements conservatively. "
            "Do not invent business rules. Flag security, architecture, production and cost risks. "
            "Any potential monetary cost must set cost_impact=true. Return only the requested JSON."
        )
        user = (
            f"Repository: {issue.repository}\nIssue: #{issue.number} {issue.title}\n"
            f"Labels: {', '.join(issue.labels)}\n\n{issue.body}"
        )
        data = self.ollama.chat_json(
            model=target.model,
            system=system,
            user=user,
            schema=TECH_LEAD_SCHEMA,
        )
        return TechLeadPlan(
            summary=data["summary"],
            affected_areas=list(data["affected_areas"]),
            risks=list(data["risks"]),
            tests=list(data["tests"]),
            cost_impact=bool(data["cost_impact"]),
            raw=data,
        )
