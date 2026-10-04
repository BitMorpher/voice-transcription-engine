"""Audio-compatible CLI and optional end-to-end video/audio pipeline."""

import argparse
import json
import math
import sys
from pathlib import Path

if __package__:
    from .media import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, MediaError, require_ffmpeg
    from .model_config import (
        DEFAULT_ASR_MODEL,
        DEFAULT_EDITING_MODEL,
        EditingOptions,
        ModelConfigurationError,
        TranscriptionOptions,
        load_hints,
    )
    from .pipeline import Pipeline, PipelineError
    from .private_output import OutputError, output_directory, write_private
    from .transcriber import ConfigurationError, Transcriber, TranscriptionError
else:
    from media import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, MediaError, require_ffmpeg
    from model_config import (
        DEFAULT_ASR_MODEL,
        DEFAULT_EDITING_MODEL,
        EditingOptions,
        ModelConfigurationError,
        TranscriptionOptions,
        load_hints,
    )
    from pipeline import Pipeline, PipelineError
    from private_output import OutputError, output_directory, write_private
    from transcriber import ConfigurationError, Transcriber, TranscriptionError


def _report(**details):
    # Only fixed messages, item indices, opaque IDs, counts, and stage statuses.
    print(json.dumps(details, sort_keys=True))


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Never forward argparse's diagnostic: recognized flags, invalid values,
        # ambiguous options and unknown arguments can all contain supplied text.
        guidance = 'Invalid command-line arguments; see --help.'
        if message.startswith('unrecognized arguments:'):
            guidance = 'Unrecognized command-line options; see --help.'
        for action in self._actions:
            name = '/'.join(action.option_strings)
            prefix = f'argument {name}: '
            if not name or not message.startswith(prefix):
                continue
            diagnostic = message[len(prefix):]
            if diagnostic.startswith('ignored explicit argument'):
                guidance = f'Option {name} does not accept a value; see --help.'
            elif diagnostic == 'expected one argument':
                guidance = f'Option {name} requires a value; see --help.'
            elif action.type is _positive_timeout:
                guidance = f'Option {name} requires a positive finite number; see --help.'
            else:
                guidance = f'Invalid or conflicting use of option {name}; see --help.'
            break
        super().error(guidance)

    def usage_error(self, message):
        """Report only fixed application guidance, never argument-derived text."""
        super().error(message)


def _positive_timeout(value):
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('Media timeout must be a positive finite number.') from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError('Media timeout must be a positive finite number.')
    return seconds


def _legacy_process(source, output, transcriber, args):
    """Retain audio CLI names and flags, with exclusive private artifact writes."""
    targets = [output / f'{source.stem}_transcription.txt']
    if args.enhance_for_reading:
        targets.append(output / f'{source.stem}_enhanced.txt')
    if args.format_as_interview:
        targets.append(output / f'{source.stem}_enhanced_interview.txt')
    if any(target.exists() or target.is_symlink() for target in targets):
        raise PipelineError('Output already exists; use a new output folder. No file was overwritten.')
    text = transcriber.transcribe(str(source))
    write_private(targets[0], text)
    stages = {'transcription': 'complete'}
    if args.enhance_for_reading:
        write_private(output / f'{source.stem}_enhanced.txt',
                      'AI readability derivative; verify against the original transcript.\n'
                      'Speaker identities and turn boundaries are unverified; no roles are inferred.\n\n'
                      + transcriber.enhance_transcription(text))
        stages['enhancement'] = 'complete'
    if args.format_as_interview:
        write_private(output / f'{source.stem}_enhanced_interview.txt',
                      'AI dialogue-layout derivative; verify against the original transcript.\n'
                      'Speaker identities and turn boundaries are unverified; no roles are inferred.\n\n'
                      + transcriber.enhance_as_interview(text))
        stages['dialogue_formatting'] = 'complete'
    return stages


def main(argv=None):
    parser = PrivateArgumentParser(prog='voice-transcribe', color=False,
                                   description='Convert local media and transcribe audio using OpenAI.')
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--input', help='Local media file or folder (pipeline mode).')
    inputs.add_argument('--input_folder', '--input-folder', help='Folder of audio files; also accepted in pipeline mode.')
    parser.add_argument('--output_folder', '--output-folder', default='private/output',
                        help='Private output directory (default: ignored private/output).')
    parser.add_argument('--pipeline', action='store_true', help='Prepare audio/video, then transcribe.')
    parser.add_argument('--extract-only', action='store_true', help='Prepare WAV audio locally; no API/key required.')
    parser.add_argument('--resume', action='store_true', help='Verify manifest checksums and skip complete pipeline stages.')
    parser.add_argument('--media-timeout', type=_positive_timeout, default=3600,
                        help='Maximum seconds per FFmpeg operation (default: 3600).')
    parser.add_argument('--model', default=DEFAULT_ASR_MODEL, help='ASR model (default: gpt-transcribe).')
    parser.add_argument('--editing-model', default=DEFAULT_EDITING_MODEL,
                        help='Opt-in faithful editing model (default: gpt-6-astra).')
    parser.add_argument('--context-file', help='Private UTF-8 file of user-supplied recording context.')
    parser.add_argument('--glossary-file', help='Private UTF-8 glossary, one expected term per line (gpt-transcribe).')
    parser.add_argument('--language', action='append', default=[],
                        help='Expected lowercase ISO 639 language code; repeat for multilingual gpt-transcribe input.')
    parser.add_argument('--audio-chunk-seconds', type=_positive_timeout, default=300,
                        help='Local audio request duration cap, 1–600 seconds (default: 300).')
    parser.add_argument('--enhance_for_reading', '--enhance-for-reading', action='store_true',
                        help='Opt in to an additional AI readability derivative; may be inaccurate.')
    parser.add_argument('--format_as_interview', action='store_true',
                        help='Legacy audio-only faithful layout alias; never assigns speaker roles.')
    args = parser.parse_args(argv)
    pipeline_mode = args.pipeline or args.extract_only
    if args.input and not pipeline_mode:
        parser.usage_error('--input requires --pipeline or --extract-only.')
    if args.resume and not pipeline_mode:
        parser.usage_error('--resume requires --pipeline or --extract-only.')
    if args.format_as_interview and pipeline_mode:
        parser.usage_error('--format_as_interview is available only in legacy audio mode.')
    if args.extract_only and args.enhance_for_reading:
        parser.usage_error('--extract-only cannot request enhancement.')
    if args.extract_only and (args.context_file or args.glossary_file or args.language):
        parser.usage_error('--extract-only cannot request transcription hints; supply them when transcribing.')

    try:
        context, keywords = load_hints(context_file=args.context_file, glossary_file=args.glossary_file)
        options = TranscriptionOptions(model=args.model, context=context, keywords=keywords,
                                       languages=tuple(args.language), chunk_seconds=args.audio_chunk_seconds)
        editing_options = EditingOptions(model=args.editing_model)
        source = Path(args.input or args.input_folder)
        if source.is_symlink() or not source.exists():
            raise PipelineError('Input is missing or is a symlink; choose an accessible local file or folder.')
        if args.input_folder and not source.is_dir():
            raise PipelineError('--input_folder must be a local directory.')
        extensions = MEDIA_EXTENSIONS if pipeline_mode else AUDIO_EXTENSIONS
        if source.is_dir():
            files = sorted(path for path in source.iterdir()
                           if path.is_file() and not path.is_symlink() and path.suffix.lower() in extensions)
        elif source.is_file() and source.suffix.lower() in extensions:
            files = [source]
        else:
            raise PipelineError('Unsupported input type or extension.')
        if not files:
            raise PipelineError('No supported media files were found (folders are scanned nonrecursively).')
        require_ffmpeg()
        output = output_directory(args.output_folder)
        transcriber = None if args.extract_only else Transcriber(media_timeout=args.media_timeout,
                                                                options=options, editing_options=editing_options)
        pipeline = Pipeline(output, resume=args.resume, media_timeout=args.media_timeout,
                            options=options, editing_options=editing_options) if pipeline_mode else None
    except (MediaError, PipelineError, ConfigurationError, OSError, ValueError) as error:
        message = str(error) if type(error) in (MediaError, PipelineError, OutputError, ConfigurationError, ModelConfigurationError) else 'Cannot access local input/output; check permissions and free space.'
        _report(status='failed', message=message)
        return 1

    failures = 0
    for index, item in enumerate(files, start=1):
        try:
            if pipeline:
                identity, stages = pipeline.process(item, transcriber=transcriber,
                                                    extract_only=args.extract_only, enhance=args.enhance_for_reading)
                _report(item=index, job=identity, status='complete', stages=stages)
            else:
                stages = _legacy_process(item, output, transcriber, args)
                _report(item=index, status='complete', stages=stages)
        except Exception as error:
            failures += 1
            message = str(error) if type(error) in (MediaError, PipelineError, TranscriptionError) else 'Processing failed; check media validity, output access, and free space.'
            _report(item=index, status='failed', message=message,
                    stages=error.stages if type(error) is PipelineError else {})
    _report(status='summary', processed=len(files), failed=failures)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
