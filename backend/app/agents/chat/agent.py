"""Tool-calling doc-chat agent (model-agnostic: drives tools via chat_json,
so it works even on deployments without native function-calling)."""
from __future__ import annotations

import json
from typing import Any

from app.agents.chat import tools as T
from app.core.logging import get_logger
from app.llm.adapters import chat_json, llm_available

log = get_logger(__name__)


def _specs_text() -> str:
    return "\n".join(
        f"- {s['name']}({', '.join(s['args'])}): {s['desc']}" for s in T.TOOL_SPECS
    )


_SYSTEM = (
    "You are DocuMorph's document assistant for a document-formatting platform. You "
    "help users understand their document, plan a new version, explain what changed "
    "between versions, report formatting/styling details, and produce styling or "
    "restyled-document artifacts — always grounded in the real document via tools.\n\n"
    "Available tools:\n{tools}\n\n"
    "Guidance:\n"
    "- Ground EVERY factual claim in a tool result; never invent document content, "
    "section numbers, or formatting values you did not see in a tool result. If the "
    "tools could not surface something, say so plainly. Call `profile_document` first for "
    "broad 'what is this / what type / what version' questions; `summarize_document` "
    "for content; `describe_formatting` for fonts/margins; `diff_documents` to compare "
    "two versions.\n"
    "- Prefer calling a tool over guessing. You may call several tools across turns.\n"
    "- Keep answers concise and skimmable. Use clean Markdown: short paragraphs, "
    "'- ' bullets, and **bold** for key values. Don't dump raw JSON; explain it.\n"
    "- COMPLIANCE MODE: if a guideline + audit findings are provided below, you are a "
    "regulatory compliance assistant. Answer 'where does my document fall short' and "
    "'how do I fix section X' from the findings context. Prefer `search_guideline` to "
    "quote the exact guideline requirement and `get_document_section` to quote the "
    "user's own text — always cite guideline section numbers like §9.2. Lead with the "
    "most severe gaps.\n\n"
    'Respond with ONE JSON object, either:\n'
    '  {{"action":"tool","tool":"<name>","args":{{...}}}}\n'
    "or, when you can answer:\n"
    '  {{"action":"final","answer":"<concise Markdown answer>"}}\n'
    "When a tool needs a 'uri' and the user is discussing the subject document, use "
    "the subject document uri."
)


def run_chat_agent(
    message: str,
    *,
    subject_uri: str | None = None,
    history: list[dict] | None = None,
    guideline_code: str | None = None,
    compliance_context: str | None = None,
    max_steps: int = 8,
) -> dict[str, Any]:
    steps: list[dict] = []
    if not llm_available():
        return {"answer": "The chat LLM is not configured.", "steps": steps}

    system = _SYSTEM.format(tools=_specs_text())
    convo = f"User message: {message}\n"
    if subject_uri:
        convo += f"Subject document uri: {subject_uri}\n"
    if guideline_code:
        convo += f"Selected guideline code: {guideline_code}\n"
    if compliance_context:
        convo += f"\nCompliance audit context (the user's document audited against the guideline):\n{compliance_context}\n"
    if history:
        convo += "Recent history:\n" + json.dumps(history[-4:], default=str)[:1500] + "\n"

    for _ in range(max_steps):
        decision = chat_json(system, convo, temperature=0.1) or {}
        if decision.get("action") == "final" or "answer" in decision and "action" not in decision:
            return {"answer": decision.get("answer", ""), "steps": steps}
        if decision.get("action") == "tool":
            name = decision.get("tool", "")
            args = decision.get("args") or {}
            # Fill in session defaults only when the LLM didn't choose a value
            # itself (e.g. comparing against a different uri or guideline).
            if subject_uri and "uri" in T.TOOL_ARGS.get(name, []) and not args.get("uri"):
                args["uri"] = subject_uri
            if guideline_code and "guideline_code" in T.TOOL_ARGS.get(name, []) and not args.get("guideline_code"):
                args["guideline_code"] = guideline_code
            fn = T.TOOLS.get(name)
            if fn is None:
                convo += f"\n[no such tool: {name}]\n"
                continue
            try:
                result = fn(**args)
            except Exception as e:  # noqa: BLE001
                result = {"error": str(e)}
            steps.append({"tool": name, "args": args, "result": result})
            convo += f"\nTool {name} result: {json.dumps(result, default=str)[:2000]}\n"
            continue
        # Unrecognized shape → treat as final.
        return {"answer": decision.get("answer") or "(no answer)", "steps": steps}

    # Step budget exhausted — ask for a best-effort answer from what we have.
    final = chat_json(
        system,
        convo
        + "\nYou have used all available tool steps. Respond with "
        '{"action":"final","answer":...}: give your best-effort answer, grounded '
        "only in the tool results above — summarize what they found, and state "
        "clearly what you could not finish or verify.",
        temperature=0.1,
    ) or {}
    return {"answer": final.get("answer", "(no answer)"), "steps": steps}
