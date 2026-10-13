"""Локальный транскрибатор звонков с маскированием персональных данных."""

__version__ = "0.1.0"


def main() -> None:
    from transcriber.cli import main as cli_main

    cli_main()
