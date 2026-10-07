"""Interview Studio command dispatch; existing workflows own their options and state."""

import argparse
import sys

from . import cli
from .batch import cli as batch_cli


def parser():
    return_value = cli.PrivateArgumentParser(
        prog='interview', color=False, allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Interview Studio: transcribe recordings, retain originals, and optionally '
                    'add speaker attribution, text polish, editorial review, and chapter drafts.',
        epilog='Commands:\n'
               '  transcribe  One recording, independent files, or ordered parts of one interview.\n'
               '  batch       Prepare and process several independent interviews.\n\n'
               'Examples:\n'
               '  interview transcribe --help\n'
               '  interview transcribe --prepare-audio --input private/input/example.mp4\n'
               '  interview batch --help\n'
               '  interview batch inventory --batch-plan private/config/batch.json\n\n'
               'Audio preparation is local. Hosted OpenAI processing can incur charges.\n'
               'voice-transcribe and voice-batch remain supported compatibility commands.\n'
               'See docs/cli-reference.md and docs/migration.md.')
    return_value.add_argument('command', choices=('transcribe', 'batch'), metavar='COMMAND',
                             help='Choose transcribe or batch; each command has its own --help.')
    return return_value


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    command_parser = parser()
    if not arguments:
        command_parser.print_help()
        return 0
    # Parse only the command. Forward every remaining token verbatim, so child
    # help, aliases, privacy diagnostics and mode gates remain authoritative.
    command = command_parser.parse_args(arguments[:1]).command
    if command == 'transcribe':
        return cli.entrypoint(arguments[1:], studio=True)
    return batch_cli.main(arguments[1:], studio=True)


if __name__ == '__main__':
    sys.exit(main())
