"""Audio-compatible CLI and optional end-to-end video/audio pipeline."""

import argparse
import json
import math
import sys
from pathlib import Path

if __package__:
    from .media import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, MediaError, require_ffmpeg
    from .pipeline import Pipeline, PipelineError
    from .private_output import OutputError, output_directory, write_private
    from .transcriber import ConfigurationError, Transcriber, TranscriptionError
else:
    from media import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, MediaError, require_ffmpeg
    from pipeline import Pipeline, PipelineError
    from private_output import OutputError, output_directory, write_private
    from transcriber import ConfigurationError, Transcriber, TranscriptionError


def _report(**details):
    # Only fixed messages, item indices, opaque IDs, counts, and stage statuses.
    print(json.dumps(details, sort_keys=True))


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
                      'AI readability derivative; verify against the original transcript.\n\n'
                      + transcriber.enhance_transcription(text))
        stages['enhancement'] = 'complete'
    if args.format_as_interview:
        write_private(output / f'{source.stem}_enhanced_interview.txt',
                      'AI dialogue-formatting derivative; verify against the original transcript.\n\n'
                      + transcriber.enhance_as_interview(text))
        stages['dialogue_formatting'] = 'complete'
    return stages


def main(argv=None):
    parser = argparse.ArgumentParser(description='Convert local media and transcribe audio using OpenAI.')
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
    parser.add_argument('--enhance_for_reading', '--enhance-for-reading', action='store_true',
                        help='Opt in to an additional AI readability derivative; may be inaccurate.')
    parser.add_argument('--format_as_interview', action='store_true',
                        help='Legacy audio-only dialogue derivative; no invented speaker turns requested.')
    args = parser.parse_args(argv)
    pipeline_mode = args.pipeline or args.extract_only
    if args.input and not pipeline_mode:
        parser.error('--input requires --pipeline or --extract-only.')
    if args.resume and not pipeline_mode:
        parser.error('--resume requires --pipeline or --extract-only.')
    if args.format_as_interview and pipeline_mode:
        parser.error('--format_as_interview is available only in legacy audio mode.')
    if args.extract_only and args.enhance_for_reading:
        parser.error('--extract-only cannot request enhancement.')

    try:
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
        transcriber = None if args.extract_only else Transcriber(media_timeout=args.media_timeout)
        pipeline = Pipeline(output, resume=args.resume, media_timeout=args.media_timeout) if pipeline_mode else None
    except (MediaError, PipelineError, ConfigurationError, OSError, ValueError) as error:
        message = str(error) if type(error) in (MediaError, PipelineError, OutputError, ConfigurationError) else 'Cannot access local input/output; check permissions and free space.'
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
