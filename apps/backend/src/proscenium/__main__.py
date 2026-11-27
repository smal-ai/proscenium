import sys
from collections.abc import Sequence

from proscenium.operator import OperatorError
from proscenium.operator.launch import legacy_serve_parser, serve


def main(argv: Sequence[str] | None = None) -> int | None:
    from proscenium.cli import COMMANDS
    from proscenium.cli import main as cli_main

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in COMMANDS:
        return cli_main(arguments)

    parser = legacy_serve_parser()
    try:
        serve(**vars(parser.parse_args(arguments)))
    except OperatorError as exc:
        parser.error(str(exc))
    return None


if __name__ == "__main__":
    raise SystemExit(main())
