import os
import re
import random
from mistralai import Mistral
from dotenv import load_dotenv
from audio_engine import speak_french, listen_french
from rich.console import Console
from rich.panel import Panel

load_dotenv()

_client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
_MODEL = "mistral-large-latest"
console = Console()

CONNECTORS = [
    "donc", "alors", "cependant", "pourtant", "néanmoins", "ensuite",
    "enfin", "ainsi", "car", "puisque", "parce que", "mais", "or",
    "en effet", "par conséquent", "d'abord", "de plus", "en revanche",
]

_NUMBER_RANGES = [
    (1, 100),
    (100, 1000),
    (1000, 100000),
]


def _ask_mistral(system: str, user: str, max_tokens: int = 256) -> str:
    response = _client.chat.complete(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.8,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()


def _extract_digits(text: str) -> str:
    """Pull all digit sequences from a string and join them."""
    return " ".join(re.findall(r"\d[\d:.,]*", text))


def _normalize_number(text: str) -> str:
    return re.sub(r"[\s.,]", "", text).lower()


# ---------------------------------------------------------------------------
# Number Drill
# ---------------------------------------------------------------------------

def run_number_drill(rounds: int = 5) -> None:
    console.print(Panel("[bold cyan]Number Drill[/bold cyan]\nListen to the sentence, then type the number you hear.", expand=False))
    score = 0

    for i in range(1, rounds + 1):
        low, high = random.choice(_NUMBER_RANGES)
        target = random.randint(low, high)

        sentence = _ask_mistral(
            system=(
                "You are a French language drill assistant. "
                "Generate ONE natural French sentence that contains the number {n}. "
                "The number must appear as digits in your response so it can be extracted. "
                "Return ONLY the sentence, nothing else."
            ).format(n=target),
            user=f"Generate a sentence using the number {target}.",
            max_tokens=80,
        )

        console.print(f"\n[bold]Round {i}/{rounds}[/bold]")
        console.print(f"[dim]Sentence: {sentence}[/dim]")
        speak_french(sentence)

        user_answer = input("Type the number you heard: ").strip()
        correct = _normalize_number(str(target))
        given = _normalize_number(user_answer)

        if given == correct:
            console.print("[green]✓ Correct![/green]")
            score += 1
        else:
            console.print(f"[red]✗ The answer was [bold]{target}[/bold][/red]")

    console.print(f"\n[bold]Score: {score}/{rounds}[/bold]")
    if score == rounds:
        console.print("[green]Perfect round![/green]")
    elif score >= rounds // 2:
        console.print("[yellow]Good effort — keep listening![/yellow]")
    else:
        console.print("[red]Keep practicing — numbers take time![/red]")


# ---------------------------------------------------------------------------
# Connector Drill
# ---------------------------------------------------------------------------

def run_connector_drill(max_turns: int = 8) -> None:
    console.print(Panel(
        "[bold cyan]Connector Drill[/bold cyan]\n"
        "Have a conversation in French. Every response MUST include a connector word "
        f"(e.g. {', '.join(CONNECTORS[:6])}, ...).\n"
        "The drill ends if you skip a connector or say 'stop'.",
        expand=False,
    ))

    system_prompt = (
        "You are a French conversation partner running a connector-word drill. "
        "Keep the conversation natural and interesting. After each student response, "
        "briefly acknowledge their connector word use (in parentheses), then continue "
        "the conversation with your own response that also uses a connector word. "
        "If the student does NOT use a connector word, respond only with: "
        "STOP: No connector detected. Remind them of examples and end the drill."
    )

    history: list[dict] = []
    opening = "Bonjour ! Parlons un peu. Qu'est-ce que tu as fait ce week-end ?"
    console.print(f"\n[bold magenta]Tutor:[/bold magenta] {opening}")
    speak_french(opening)
    history.append({"role": "assistant", "content": opening})

    for turn in range(max_turns):
        console.print("\n[bold blue]You (speak or type):[/bold blue] ", end="")
        user_input = listen_french()
        console.print(f"[blue]{user_input}[/blue]")

        if any(w in user_input.lower() for w in ("stop", "au revoir", "arrête")):
            console.print("[yellow]Drill ended by user.[/yellow]")
            break

        history.append({"role": "user", "content": user_input})

        response = _client.chat.complete(
            model=_MODEL,
            messages=[{"role": "system", "content": system_prompt}, *history],
            temperature=0.7,
            max_tokens=200,
        ).choices[0].message.content.strip()

        console.print(f"\n[bold magenta]Tutor:[/bold magenta] {response}")
        speak_french(response)
        history.append({"role": "assistant", "content": response})

        if response.upper().startswith("STOP:"):
            break

    console.print("\n[bold]Connector drill complete.[/bold]")
