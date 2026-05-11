import os
from mistralai import Mistral
from dotenv import load_dotenv
from document_engine import get_tutor_context

load_dotenv()

_client = Mistral(api_key=os.environ.get("MISTRAL_API_KEY", "unset"))
_MODEL = "mistral-large-latest"

def set_api_key(key: str):
    global _client
    _client = Mistral(api_key=key)

_BASE_SYSTEM_PROMPT = """You are a conversational French language tutor speaking with a beginner/intermediate learner.

Conversation style:
- Keep responses short — 2 to 4 sentences maximum. Do not lecture.
- Ask one follow-up or clarifying question at the end of most replies to keep dialogue going.
- Respond in a natural mix of French and English. Use more French as confidence builds.

When the student makes a mistake:
- Do not just silently correct and move on. Ask them why they said it that way, or invite them to try again: "Tu voulais dire... ?" or "Est-ce que tu peux reformuler ?"
- If what they said is unclear or doesn't make sense in context, ask them to clarify before responding as though you understood.
- If they mix languages awkwardly or use the wrong register, gently name it: "That works in English but in French we'd say..."

When the student says something nonsensical or out of context:
- Do not invent a meaning for it. Say you didn't quite understand and ask them to rephrase: "Je ne suis pas sûr de comprendre — tu peux expliquer ce que tu voulais dire ?"

Never pad replies with filler praise like "Great job!" or "Excellent!". React naturally."""

_SCENARIO_PROMPT = """You are roleplaying a real-world French scenario with a language learner.

Rules:
- Stay in character as a native French speaker throughout. Do not break character to explain grammar.
- Speak mostly French. Use English only if the student is completely lost.
- Keep your turns short — 1 to 3 sentences — so the student has to respond often.
- If the student's French doesn't make sense in the scenario, react as a real person would: look confused, ask them to repeat, or say you didn't understand. Do not just accept nonsensical input.
- After each exchange, either react to what they said or ask something that moves the scenario forward.

Scenario: {topic}"""

_DRILL_PROMPT = """You are running a focused French language drill on: {topic}

Rules:
- Present one item at a time. Wait for the student's response before continuing.
- If the answer is correct, confirm briefly and move on.
- If the answer is wrong or confused, do not just give the answer. Ask a guiding question first: "Presque — pense au genre du nom. Tu essaies encore ?"
- If the student seems to misunderstand the exercise itself, clarify the format with one example then ask them to try.
- Keep the pace steady. No lengthy explanations unless the student asks."""

_RAG_PROMPT_TEMPLATE = """You are a French tutor working through materials provided by the student's own teacher.

Rules:
- Base this lesson ONLY on the material below. Do not introduce outside vocabulary or topics.
- Be conversational, not a lecturer. Explain concepts in short chunks and check understanding with a question after each one.
- If the student says something that contradicts the material or doesn't make sense, point it out and ask them to look at the relevant section again.

Teacher's material:
{context}"""


def _build_system_prompt(session_mode: str, topic: str) -> str:
    if session_mode == "SCENARIO":
        return _SCENARIO_PROMPT.format(topic=topic)
    if session_mode == "DRILL":
        return _DRILL_PROMPT.format(topic=topic)
    if session_mode == "RAG":
        context = get_tutor_context()
        if not context:
            return (
                f"{_BASE_SYSTEM_PROMPT}\n\n"
                "Note: No tutor files were found in the uploads folder. "
                "Proceeding with general tutoring."
            )
        return _RAG_PROMPT_TEMPLATE.format(context=context)
    return _BASE_SYSTEM_PROMPT


def get_response(
    conversation_history: list[dict],
    session_mode: str = "CHAT",
    topic: str = "general conversation",
    pronunciation_note: str = "",
) -> str:
    """
    Takes the full conversation history and returns the tutor's next reply.

    conversation_history format:
        [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]

    pronunciation_note: injected at the end of the system prompt when a sub-prompt
    analysis has identified a likely mispronunciation in the student's last turn.
    """
    system_prompt = _build_system_prompt(session_mode, topic)
    if pronunciation_note:
        system_prompt = f"{system_prompt}\n\n{pronunciation_note}"

    response = _client.chat.complete(
        model=_MODEL,
        messages=[{"role": "system", "content": system_prompt}, *conversation_history],
        temperature=0.7,
        max_tokens=512,
    )
    return response.choices[0].message.content.strip()
