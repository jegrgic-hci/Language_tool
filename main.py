import os
import sys
import pygame
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

from audio_engine import speak_french, listen_french
from router import route
from tutor import get_response
from drills import run_number_drill, run_connector_drill

load_dotenv()

console = Console()

EXIT_PHRASES = {"au revoir", "stop", "quit", "exit", "arrête", "arrete"}
DRILL_TOPICS_NUMBER = {"number", "numbers", "chiffre", "chiffres", "nombre", "nombres"}
DRILL_TOPICS_CONNECTOR = {"connector", "connectors", "transition", "transitions"}


def _is_exit(text: str) -> bool:
    return any(phrase in text.lower() for phrase in EXIT_PHRASES)


def _greet() -> None:
    console.print(Panel(
        "[bold green]Bienvenue ![/bold green]\n"
        "French Language Tutor — powered by Mistral\n\n"
        "[dim]Say or type 'Au revoir' or 'Stop' to end the session.[/dim]",
        title="🇫🇷 French Tutor",
        expand=False,
    ))
    opening = "Bonjour ! Sur quoi aimerais-tu travailler aujourd'hui ?"
    console.print(f"\n[bold magenta]Tutor:[/bold magenta] {opening}")
    speak_french(opening)


def _run_session(session_mode: str, topic: str) -> None:
    history: list[dict] = []

    if session_mode == "DRILL":
        topic_lower = topic.lower()
        if any(w in topic_lower for w in DRILL_TOPICS_NUMBER):
            run_number_drill()
        elif any(w in topic_lower for w in DRILL_TOPICS_CONNECTOR):
            run_connector_drill()
        else:
            run_number_drill()
        return

    mode_labels = {
        "SCENARIO": f"[bold yellow]Scenario:[/bold yellow] {topic}",
        "RAG": "[bold yellow]Mode:[/bold yellow] Tutor document review",
        "CHAT": "[bold yellow]Mode:[/bold yellow] Free conversation",
    }
    console.print(f"\n{mode_labels.get(session_mode, '')}")

    while True:
        console.print("\n[bold blue]You:[/bold blue] ", end="")
        user_input = listen_french()
        console.print(f"[blue]{user_input}[/blue]")

        if _is_exit(user_input):
            farewell = "Au revoir ! Bonne continuation !"
            console.print(f"\n[bold magenta]Tutor:[/bold magenta] {farewell}")
            speak_french(farewell)
            sys.exit(0)

        new_mode, new_topic = route(user_input)
        if new_mode != session_mode:
            console.print(f"\n[dim]Switching mode → {new_mode} ({new_topic})[/dim]")
            _run_session(new_mode, new_topic)
            return

        history.append({"role": "user", "content": user_input})
        reply = get_response(history, session_mode=session_mode, topic=topic)
        history.append({"role": "assistant", "content": reply})

        console.print(f"\n[bold magenta]Tutor:[/bold magenta] {reply}")
        speak_french(reply)


def main() -> None:
    pygame.init()

    _greet()

    console.print("\n[bold blue]You:[/bold blue] ", end="")
    first_input = listen_french()
    console.print(f"[blue]{first_input}[/blue]")

    if _is_exit(first_input):
        speak_french("Au revoir !")
        return

    session_mode, topic = route(first_input)
    console.print(Rule(f"[dim]Mode: {session_mode} | Topic: {topic}[/dim]"))

    _run_session(session_mode, topic)


if __name__ == "__main__":
    main()
