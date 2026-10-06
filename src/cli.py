"""Audio-compatible CLI and optional end-to-end video/audio pipeline."""

import argparse
import json
import math
import sys
from pathlib import Path

if __package__:
    from .interview_attribution import InterviewOptions, DIARIZATION_MODEL
    from .progress import CURRENT, Reporter, interruptions, LogError
    from .provider_control import CURRENT_CONTROL, ProviderControl
    from .author_review import ReviewError, ReviewOptions
    from .author_workflow import AuthorOptions, AuthorWorkflowError
    from .chapters import ChapterOptions
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
    from .ordered_interview import OrderedInterview
    from .private_output import OutputError, output_directory, write_private
    from .transcriber import ConfigurationError, Transcriber, TranscriptionError
else:
    from interview_attribution import InterviewOptions, DIARIZATION_MODEL
    from progress import CURRENT, Reporter, interruptions, LogError
    from provider_control import CURRENT_CONTROL, ProviderControl
    from author_review import ReviewError, ReviewOptions
    from author_workflow import AuthorOptions, AuthorWorkflowError
    from chapters import ChapterOptions
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
    from ordered_interview import OrderedInterview
    from private_output import OutputError, output_directory, write_private
    from transcriber import ConfigurationError, Transcriber, TranscriptionError


def _report(**details):
    # Only fixed messages, item indices, opaque IDs, counts, and stage statuses.
    reporter = CURRENT.get()
    if reporter is not None:
        # Batch context uses original plan positions; the inner interview is one item.
        if 'item' in reporter.context:
            details.pop('item', None)
        reporter.emit(**details)
    else:
        print(json.dumps(details, sort_keys=True), flush=True)


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


def _execute(argv=None, *, approved_review=None, interview_options_override=None,
         approved_attributed_review=None, attributed_only=False, require_raw=False):
    parser = PrivateArgumentParser(prog='voice-transcribe', color=False, allow_abbrev=False,
                                   description='Convert local media and transcribe audio using OpenAI.')
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--input', help='Local media file or folder (pipeline mode).')
    inputs.add_argument('--input-folder', '--input_folder', dest='input_folder', help='Folder of audio files; also accepted in pipeline mode. Underscore spelling is a deprecated compatibility alias.')
    inputs.add_argument('--interview-manifest', help='Version 1 JSON manifest: ordered recordings from one interview; requires --workflow.')
    parser.add_argument('--output-folder', '--output_folder', dest='output_folder', default='private/output',
                        help='Private output directory (default: private/output). Underscore spelling is a deprecated compatibility alias.')
    parser.add_argument('--pipeline', action='store_true', help='Prepare audio/video, then transcribe.')
    parser.add_argument('--extract-only', action='store_true', help='Prepare WAV audio locally; no API/key required.')
    parser.add_argument('--resume', action='store_true', help='Verify manifest checksums and skip complete pipeline stages.')
    parser.add_argument('--media-timeout', type=_positive_timeout, default=3600,
                        help='Maximum seconds per FFmpeg operation (default: 3600).')
    parser.add_argument('--transcription-model', '--model', dest='model', default=DEFAULT_ASR_MODEL,
                        help='Original speech-to-text model (default: gpt-transcribe); --model remains a compatibility alias.')
    parser.add_argument('--editing-model', default=DEFAULT_EDITING_MODEL,
                        help='Opt-in faithful editing model (default: gpt-6-astra).')
    parser.add_argument('--context-file', help='Private UTF-8 file of user-supplied recording context.')
    parser.add_argument('--glossary-file', help='Private UTF-8 glossary, one expected term per line (gpt-transcribe).')
    parser.add_argument('--language', action='append', default=[],
                        help='Expected lowercase ISO 639 language code; repeat for multilingual gpt-transcribe input.')
    parser.add_argument('--audio-chunk-seconds', type=_positive_timeout, default=300,
                        help='Local audio request duration cap, 1–600 seconds (default: 300).')
    parser.add_argument('--enhance-for-reading', '--enhance_for_reading', dest='enhance_for_reading', action='store_true',
                        help='Additional faithful readability derivative; may be inaccurate. Underscore spelling is deprecated.')
    parser.add_argument('--format-as-interview', '--format_as_interview', dest='format_as_interview', action='store_true',
                        help='Legacy audio-only layout; never assigns roles. Underscore spelling is deprecated; use --enhance-for-reading for new workflows.')
    parser.add_argument('--workflow', action='store_true',
                        help='Author workflow: raw, optional polish, review, selectable chapter drafts.')
    parser.add_argument('--interview', action='store_true',
                        help='Add a separate diarized output family; requires --workflow and both names. Adds provider calls for selected stages.')
    parser.add_argument('--interviewer-name', help='Local display name; does not identify a voice.')
    parser.add_argument('--interviewee-name', help='Local display name; does not identify a voice.')
    parser.add_argument('--speaker-map', action='append', default=[],
                        help='User-confirmed mapping PART:REQUEST:LABEL=interviewer or =interviewee; repeat. Unmapped speakers remain unidentified.')
    parser.add_argument('--diarization-chunk-seconds', type=_positive_timeout,
                        help='Independent speaker-pass duration (1–600 seconds); omitted uses the original chunk duration.')
    parser.add_argument('--confirm-speaker-mappings', action='store_true',
                        help='Confirm supplied mappings were checked against the explicit new diarization chunk scopes.')
    parser.add_argument('--speaker-model', '--interview-model', dest='interview_model', default=DIARIZATION_MODEL,
                        help='Voice separation model (gpt-4o-transcribe-diarize); does not identify people. Requires --interview; --interview-model remains an alias. Original speech-to-text uses --transcription-model.')
    parser.add_argument('--media-type', choices=('auto', 'audio', 'video'), default='auto',
                        help='Workflow media type (default: auto by supported extension).')
    parser.add_argument('--stages', default='raw,polish,review',
                        help='Workflow stages, comma separated: raw,polish,review,chapters. Raw always retained.')
    parser.add_argument('--chapters', choices=('none', 'interview', 'narrative', 'both'), default='none',
                        help='Select chapter drafts; implies review (default: none).')
    parser.add_argument('--narrative-person', choices=('first', 'third'), default='first',
                        help='Conservative narrative testimony framing (default: first).')
    parser.add_argument('--author-model', default=DEFAULT_EDITING_MODEL,
                        help='Review/chapter model (default: gpt-6-astra).')
    parser.add_argument('--draft-with-unresolved-high', action='store_true',
                        help='Explicitly allow labeled drafts with unresolved high-priority findings.')
    parser.add_argument('--log-directory', help='Private JSONL logs (default: output/execution-logs).')
    parser.add_argument('--heartbeat-seconds', type=_positive_timeout, default=30,
                        help='Idle heartbeat interval in seconds (default: 30).')
    parser.add_argument('--provider-timeout', type=_positive_timeout, default=120,
                        help='SDK request timeout seconds (default: 120; not a whole-run deadline).')
    parser.add_argument('--provider-retries', type=int, choices=range(0, 6), default=2,
                        help='SDK retry limit 0–5 (default: 2). Retried requests may incur charges.')
    parser.add_argument('--max-provider-requests', type=int,
                        help='Maximum new SDK operations across all stages; requires --provider-retries 0. One is a bounded diagnostic, not a complete transcript.')
    parser.add_argument('--max-run-seconds', type=_positive_timeout,
                        help='Stop starting provider calls after this elapsed time; requires zero retries. In-flight I/O is timeout-bounded, not cancelled at this deadline.')
    parser.add_argument('--provider-failure-limit', type=int, default=2,
                        help='Stop admission after consecutive failures per endpoint/model (default: 2); definite account/configuration failures stop immediately.')
    args = parser.parse_args(argv)
    supplied_flags = {arg.split('=', 1)[0] for arg in (argv if argv is not None else sys.argv[1:])}
    if args.interview and (not args.workflow or args.extract_only):
        parser.usage_error('--interview requires --workflow and cannot use --extract-only.')
    if not args.interview and supplied_flags & {'--interviewer-name', '--interviewee-name', '--speaker-map', '--interview-model', '--speaker-model', '--diarization-chunk-seconds', '--confirm-speaker-mappings'}:
        parser.usage_error('Speaker naming options require --interview.')
    reporter = CURRENT.get()
    if reporter is not None:
        reporter.heartbeat = args.heartbeat_seconds
        try:
            reporter.start(args.log_directory or Path(args.output_folder) / 'execution-logs')
        except Exception:
            _report(status='failed')
            return 1
    author_options = None
    if args.interview_manifest is not None:
        if not args.workflow:
            parser.usage_error('--interview-manifest requires --workflow.')
        if args.media_type != 'auto':
            parser.usage_error('Set each recording media_type in the interview manifest.')
    if args.workflow:
        requested = args.stages.split(',')
        if (not requested or any(stage not in {'raw', 'polish', 'review', 'chapters'} for stage in requested)
                or len(requested) != len(set(requested))):
            parser.usage_error('--stages must be a comma-separated selection of raw,polish,review,chapters.')
        if args.extract_only:
            parser.usage_error('--workflow cannot be combined with --extract-only; run extraction separately.')
        if 'chapters' in requested and args.chapters == 'none':
            parser.usage_error('The chapters stage requires --chapters interview, narrative, or both.')
        styles = ('interview', 'narrative') if args.chapters == 'both' else (() if args.chapters == 'none' else (args.chapters,))
        if args.draft_with_unresolved_high and not styles:
            parser.usage_error('--draft-with-unresolved-high requires a chapter selection.')
    else:
        author_flags = {'--media-type', '--stages', '--chapters', '--narrative-person', '--author-model',
                        '--draft-with-unresolved-high'}
        supplied_flags = {arg.split('=', 1)[0] for arg in (argv if argv is not None else sys.argv[1:])}
        if supplied_flags & author_flags:
            parser.usage_error('Author options require --workflow.')
    pipeline_mode = args.pipeline or args.extract_only or args.workflow
    if args.input is not None and not pipeline_mode:
        parser.usage_error('--input requires --pipeline or --extract-only; --workflow also accepts local media.')
    if args.resume and not pipeline_mode:
        parser.usage_error('--resume requires --pipeline, --extract-only, or --workflow.')
    if args.format_as_interview and pipeline_mode:
        parser.usage_error('--format-as-interview is available only in legacy audio mode.')
    if args.extract_only and args.enhance_for_reading:
        parser.usage_error('--extract-only cannot request enhancement.')
    if args.extract_only and (args.context_file or args.glossary_file or args.language):
        parser.usage_error('--extract-only cannot request transcription hints; supply them when transcribing.')

    try:
        if CURRENT_CONTROL.get() is None:
            CURRENT_CONTROL.set(ProviderControl(max_requests=args.max_provider_requests,
                max_seconds=args.max_run_seconds, failure_limit=args.provider_failure_limit,
                retries=args.provider_retries))
        if interview_options_override is not None and (type(interview_options_override) is not InterviewOptions
                or not args.workflow or args.interview_manifest is None or args.interview
                or supplied_flags & {'--interviewer-name', '--interviewee-name', '--speaker-map', '--interview-model', '--speaker-model'}):
            raise ModelConfigurationError('Internal batch interview settings require an ordered workflow without competing speaker flags.')
        if attributed_only and (interview_options_override is None or not args.workflow
                or args.interview_manifest is None or 'review' not in requested):
            raise ModelConfigurationError('Independent attributed processing requires a batch text phase.')
        if require_raw and (not args.workflow or args.interview_manifest is None or 'review' not in requested):
            raise ModelConfigurationError('Existing raw is required only for an ordered text phase.')
        interview_options = interview_options_override or (InterviewOptions(args.interviewer_name, args.interviewee_name,
            tuple(args.speaker_map), args.interview_model,
            diarization_chunk_seconds=args.diarization_chunk_seconds,
            mappings_reconfirmed=args.confirm_speaker_mappings) if args.interview else None)
        if args.confirm_speaker_mappings and args.diarization_chunk_seconds is None:
            raise ModelConfigurationError('Speaker mapping confirmation requires explicit diarization chunks.')
        selected_input = args.input if args.input is not None else args.input_folder
        if selected_input == '':
            raise PipelineError('Input path must not be empty; choose an accessible local file or folder.')
        context, keywords = load_hints(context_file=args.context_file, glossary_file=args.glossary_file)
        options = TranscriptionOptions(model=args.model, context=context, keywords=keywords,
                                       languages=tuple(args.language), chunk_seconds=args.audio_chunk_seconds)
        if reporter is not None and not reporter.configuration:
            reporter.emit(status='configuration', configuration={
                'asr_model': options.model, 'editing_model': args.editing_model, 'author_model': args.author_model,
                'diarization_model': interview_options.model if interview_options else DIARIZATION_MODEL,
                'audio_chunk_seconds': options.chunk_seconds,
                'diarization_chunk_seconds': (interview_options.diarization_chunk_seconds if interview_options else None) or options.chunk_seconds,
                'provider_timeout': args.provider_timeout, 'provider_retries': args.provider_retries,
                'max_provider_requests': args.max_provider_requests, 'max_run_seconds': args.max_run_seconds,
                'provider_failure_limit': args.provider_failure_limit, 'interview': bool(interview_options),
                'context_supplied': bool(args.context_file), 'glossary_supplied': bool(args.glossary_file),
                'language_hint_count': len(options.languages)})
        if interview_options and (len(args.language) > 1 or any(len(code) != 2 for code in args.language)):
            raise ModelConfigurationError('Additional interview diarization accepts at most one ISO 639-1 language hint; omit hints for automatic detection.')
        editing_options = EditingOptions(model=args.editing_model)
        if args.workflow:
            author_options = AuthorOptions(
                review='review' in requested or bool(styles),
                review_options=ReviewOptions(model=args.author_model),
                chapter_options=ChapterOptions(model=args.author_model, styles=styles,
                                               person=args.narrative_person) if styles else None,
                allow_unresolved_high=args.draft_with_unresolved_high,
            )
        if args.interview_manifest is not None:
            interview = OrderedInterview(args.interview_manifest, args.output_folder,
                options=options, editing_options=editing_options, author_options=author_options,
                resume=args.resume, media_timeout=args.media_timeout,
                enhance=args.enhance_for_reading or 'polish' in requested,
                interview_options=interview_options,
                progress=lambda stage, status: _report(status='progress', stage=stage, stage_status=status))
            interview.preflight(original=not attributed_only, require_raw=require_raw or attributed_only)
            require_ffmpeg()
            transcriber = Transcriber(media_timeout=args.media_timeout, options=options,
                                      editing_options=editing_options,
                                      provider_timeout=args.provider_timeout, provider_retries=args.provider_retries)
            identity, stages = interview.process(transcriber=transcriber, approved_review=approved_review,
                                                  approved_attributed_review=approved_attributed_review,
                                                  attributed_only=attributed_only, require_raw=require_raw)
            _report(job=identity, status='complete', stages=stages)
            _report(status='summary', processed=1, failed=0)
            return 0
        source = Path(selected_input)
        if source.is_symlink() or not source.exists():
            raise PipelineError('Input is missing or is a symlink; choose an accessible local file or folder.')
        if args.input_folder is not None and not source.is_dir():
            raise PipelineError('--input-folder must be a local directory.')
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
        if args.workflow and args.media_type != 'auto':
            allowed = AUDIO_EXTENSIONS if args.media_type == 'audio' else MEDIA_EXTENSIONS - AUDIO_EXTENSIONS
            if any(item.suffix.lower() not in allowed for item in files):
                raise PipelineError('Selected media type does not match the supported input extensions.')
        require_ffmpeg()
        output = output_directory(args.output_folder)
        transcriber = None if args.extract_only else Transcriber(media_timeout=args.media_timeout,
                                                                options=options, editing_options=editing_options,
                                      provider_timeout=args.provider_timeout, provider_retries=args.provider_retries)
        pipeline = Pipeline(output, resume=args.resume, media_timeout=args.media_timeout,
                            options=options, editing_options=editing_options,
                            author_options=author_options,
                            interview_options=interview_options,
                            progress=(lambda stage, status: _report(item=index, status='progress',
                                stage=stage, stage_status=status)) if CURRENT.get() is not None or args.workflow else None) if pipeline_mode else None
    except (MediaError, PipelineError, ConfigurationError, OSError, ValueError, AuthorWorkflowError, ReviewError, TranscriptionError) as error:
        message = str(error) if type(error) in (MediaError, PipelineError, OutputError, ConfigurationError, ModelConfigurationError, AuthorWorkflowError, ReviewError) else 'Cannot access local input/output; check permissions and free space.'
        control = CURRENT_CONTROL.get()
        limited = control is not None and control.reason in {'request_limit', 'start_deadline'}
        _report(status='incomplete' if limited else 'failed', message=message,
                stop_reason=control.reason if control else None,
                **({'stages': error.stages} if type(error) is PipelineError and error.stages else {}))
        return 1

    failures = unattempted = incomplete = 0
    for index, item in enumerate(files, start=1):
        control = CURRENT_CONTROL.get()
        if control is not None and control.reason:
            _report(item=index, status='not_attempted', stop_reason=control.reason)
            failures += 1
            unattempted += 1
            continue
        try:
            if pipeline:
                identity, stages = pipeline.process(item, transcriber=transcriber,
                                                    extract_only=args.extract_only,
                                                    enhance=args.enhance_for_reading or (args.workflow and 'polish' in requested))
                _report(item=index, job=identity, status='complete', stages=stages)
            else:
                stages = _legacy_process(item, output, transcriber, args)
                _report(item=index, status='complete', stages=stages)
        except Exception as error:
            failures += 1
            message = str(error) if type(error) in (MediaError, PipelineError, TranscriptionError) else 'Processing failed; check media validity, output access, and free space.'
            control = CURRENT_CONTROL.get()
            limited = control is not None and control.reason in {'request_limit', 'start_deadline'}
            incomplete += int(limited)
            _report(item=index, status='incomplete' if limited else 'failed', message=message,
                    stop_reason=control.reason if control else None,
                    stages=error.stages if type(error) is PipelineError else {})
    _report(status='summary', processed=len(files) - unattempted, failed=failures - incomplete - unattempted,
            incomplete=incomplete, not_attempted=unattempted)
    return 1 if failures else 0


def main(argv=None, *, approved_review=None, interview_options_override=None,
         approved_attributed_review=None, attributed_only=False, require_raw=False):
    token = CURRENT_CONTROL.set(CURRENT_CONTROL.get())
    try:
        return _execute(argv, approved_review=approved_review,
            interview_options_override=interview_options_override,
            approved_attributed_review=approved_attributed_review, attributed_only=attributed_only,
            require_raw=require_raw)
    finally:
        CURRENT_CONTROL.reset(token)


def entrypoint(argv=None):
    reporter = Reporter(sys.stdout)
    reporter.context = {'scope': 'interview'}
    token = CURRENT.set(reporter)
    control_token = CURRENT_CONTROL.set(None)
    try:
        with interruptions():
            code = main(argv)
    except KeyboardInterrupt:
        reporter.emit(status='interrupted')
        code = 130
    except Exception as error:
        reporter.emit(status='failed', message=str(error) if type(error) is LogError else None)
        code = 1
    finally:
        reporter.close()
        CURRENT.reset(token)
        CURRENT_CONTROL.reset(control_token)
    return code or int(reporter.failed)


if __name__ == '__main__':
    sys.exit(entrypoint())
