"""Evals and simulations — test an agent before a real customer does.

Vapi's evals/simulations, rebuilt on our own stack. A test case is either:

  script    fixed caller lines, played in order
  simulate  an AI caller with a persona and a goal, improvising up to N turns

Either way the agent answers through the exact pipeline a real call uses —
same prompt, knowledge base, tools — but tools run DRY (a test never creates a
booking). A judge model then reads the transcript and the tool log and passes
or fails it against the case's rubric.
"""

import logging

import brain
import db
import kb
import webcall

log = logging.getLogger("voiceflow.evals")

CALLER_SYSTEM = """You are role-playing a CUSTOMER phoning a business, to test its phone agent.

Who you are and what you want:
{persona}

Rules:
- Speak as the customer only, one or two short spoken sentences per turn.
- Use the language the persona implies (Hindi/Hinglish/English).
- Pursue your goal naturally; react to what the agent actually says.
- When your goal is met or clearly cannot be, say goodbye and add the token [END].
"""

JUDGE_SYSTEM = """You grade a test conversation between a customer and a business's phone agent.

Pass criteria (the rubric):
{rubric}

You get the transcript and the list of tools the agent called. Judge ONLY
against the rubric. Return JSON: {{"pass": true or false, "reason": "one or two sentences"}}"""


async def _caller_line(persona: str, convo: list[tuple[str, str]]) -> str:
    """The simulated caller's next line. Roles are flipped: to this model the
    agent is the 'user' and it plays the 'assistant'."""
    msgs = [{"role": "system", "content": CALLER_SYSTEM.format(persona=persona)}]
    for who, text in convo:
        msgs.append({"role": "assistant" if who == "Caller" else "user", "content": text})
    if not convo:
        msgs.append({"role": "user", "content": "(The agent has picked up. Speak first.)"})
    msg = await brain._chat_once(msgs, with_tools=False)
    return (msg.get("content") or "").strip()


async def _judge(rubric: str, transcript: str, tool_log: list[dict]) -> tuple[bool, str]:
    calls = "\n".join(f"- {t['name']}({t['args']})" for t in tool_log) or "(none)"
    msg = await brain._chat_once([
        {"role": "system", "content": JUDGE_SYSTEM.format(rubric=rubric)},
        {"role": "user", "content": f"Transcript:\n{transcript}\n\nTools called:\n{calls}"},
    ], with_tools=False)
    verdict = brain._extract_json(msg.get("content") or "")
    if not isinstance(verdict.get("pass"), bool):
        return False, "Judge gave no clear verdict — treat as a failure and re-run."
    return verdict["pass"], str(verdict.get("reason") or "")


async def run_case(case: dict) -> dict:
    """Play one case end to end, judge it, store and return the run."""
    session = webcall.Session(case.get("agent_id"))  # same prompt a live call gets
    agent_name = session.agent.get("name") or "Agent"
    ctx = {"channel": "eval", "agent_id": session.agent.get("id"), "dry_run": True}
    convo: list[tuple[str, str]] = [(agent_name, session.greeting)]
    history: list[dict] = [{"role": "assistant", "content": session.greeting}]

    if case["mode"] == "script":
        lines = [ln.strip() for ln in (case.get("turns") or "").splitlines() if ln.strip()]
    else:
        lines = None

    for i in range(len(lines) if lines is not None else max(1, int(case.get("max_turns") or 4))):
        said = lines[i] if lines is not None else await _caller_line(case.get("persona") or "", convo)
        ended = "[END]" in said
        said = said.replace("[END]", "").strip()
        if said:
            convo.append(("Caller", said))
        if ended or not said:
            break
        try:
            answer = await brain.reply(
                "", said, history=history, prompt=session.prompt,
                kb_context=kb.context_block(ctx["agent_id"], said), tools_ctx=ctx,
                agent_id=ctx["agent_id"],
            )
        except Exception as e:  # noqa: BLE001 - a failed turn is itself a result
            answer = f"[agent error: {e}]"
        convo.append((agent_name, answer))
        history += [{"role": "user", "content": said}, {"role": "assistant", "content": answer}]
        del history[:-6]  # same window a live call uses

    transcript = "\n".join(f"{who}: {text}" for who, text in convo)
    tool_log = ctx.get("_tool_log", [])
    try:
        passed, reason = await _judge(case["rubric"], transcript, tool_log)
    except Exception as e:  # noqa: BLE001
        passed, reason = False, f"Judge unavailable: {e}"
    return db.add_eval_run(case["id"], passed, reason, transcript, tool_log)

