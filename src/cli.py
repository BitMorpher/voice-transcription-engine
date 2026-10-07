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
        REASONING_EFFORTS,
        TEXT_PROFILES,
        text_profile_settings,
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
        REASONING_EFFORTS,
        TEXT_PROFILES,
        text_profile_settings,
    )
    from pipeline import Pipeline, PipelineError
    from ordered_interview import OrderedInterview
    from private_output import OutputError, output_directory, write_private
    from transcriber import ConfigurationError, Transcriber, TranscriptionError


def _in_batch(reporter):
    """Nested interview calls retain the coordinator's batch position marker."""
    return reporter is not None and (reporter.context.get('scope') == 'batch'
                                    or 'batch_position' in reporter.context)


def _report(**details):
    # Only fixed messages, item indices, opaque IDs, counts, and stage statuses.
    reporter = CURRENT.get()
    if reporter is not None:
        # Batch context uses original plan positions; the inner interview is one item.
        if 'item' in reporter.context:
            details.pop('item', None)
            if _in_batch(reporter):
                # Inner interview results must not advance the outer batch bar.
                details.pop('finished', None)
                details.pop('selected', None)
        reporter.emit(**details)
    else:
        print(json.dumps(details, sort_keys=True), flush=True)


class PrivateArgumentParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        # Resolve presets after all flags: each explicit model/effort wins even
        # when the preset comes later. Both public entrypoints share this rule.
        result = super().parse_args(args, namespace)
        if hasattr(result, 'text_profile'):
            supplied = self.supplied_options(args)
            for name, value in text_profile_settings(result.text_profile).items():
                if name not in supplied:
                    setattr(result, name, value)
        return result

    def supplied_options(self, argv=None):
        """Resolve every declared spelling to its destination before mode checks."""
        arguments = sys.argv[1:] if argv is None else argv
        return {self._option_string_actions[flag].dest
                for value in arguments
                if (flag := value.split('=', 1)[0]) in self._option_string_actions}

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
        self.usage_error(guidance)

    def usage_error(self, message):
        """Report only fixed application guidance, never argument-derived text."""
        reporter = CURRENT.get()
        if reporter is not None:
            # stdout/stderr may share a cursor. Stop heartbeats and clear the
            # live panel before argparse moves it to print usage and diagnostics.
            reporter.close()
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


def transcription_parser(*, studio=False):
    """Build grouped help while keeping existing scripts and destination names."""
    command = 'interview transcribe' if studio else 'voice-transcribe'
    parser = PrivateArgumentParser(prog=command, color=False, allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Interview Studio: turn local audio or video into a transcript, with optional text polish, review, and chapter drafts.',
        epilog='Examples:\n'
               f'  {command} --pipeline --input private/input/example.mp4\n'
               f'  {command} --prepare-audio --input private/input/example.mp4\n'
               f'  {command} --author-workflow --recordings-list private/config/interview.json --steps raw,review\n\n'
               'Audio preparation runs locally. Transcription and selected AI text steps send material to OpenAI and can incur charges.\n'
               'Previous option spellings remain supported. See the README and docs/cli-reference.md for the full pipeline guide.')
    files = parser.add_argument_group('Input and output')
    inputs = files.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--input', metavar='FILE_OR_FOLDER', help='Local media file or folder (pipeline mode).')
    inputs.add_argument('--input-folder', '--input_folder', dest='input_folder',
                        metavar='FOLDER', help='Folder of separate audio files; also works with --pipeline or --author-workflow.')
    inputs.add_argument('--recordings-list', '--interview-manifest', dest='interview_manifest',
                        metavar='FILE', help='Version 1 JSON list giving the order of recordings from one interview; requires --author-workflow.')
    files.add_argument('--output-folder', '--output_folder', dest='output_folder', default='private/output',
                       metavar='FOLDER', help='Private folder for generated files (default: private/output).')
    files.add_argument('--media-type', choices=('auto', 'audio', 'video'), default='auto',
                       metavar='TYPE', help='Expected input type for --author-workflow (default: auto from file extensions).')
    steps = parser.add_argument_group('Processing steps')
    steps.add_argument('--pipeline', action='store_true', help='Prepare audio from local audio/video, then transcribe it.')
    steps.add_argument('--prepare-audio', '--extract-only', dest='extract_only', action='store_true',
                       help='Create WAV audio locally and stop; no OpenAI key or requests needed.')
    steps.add_argument('--author-workflow', '--workflow', dest='workflow', action='store_true',
                       help='Keep the original transcript, then run the selected text steps (default: raw,polish,review).')
    steps.add_argument('--steps', '--stages', dest='stages', default='raw,polish,review',
                       metavar='STEPS', help='Comma-separated author steps: raw (transcribe), polish (layout), review (flag passages), chapters (draft). Raw is always kept.')
    steps.add_argument('--polish-text', '--enhance-for-reading', '--enhance_for_reading', dest='enhance_for_reading', action='store_true',
                       help='Create a separate text copy with punctuation, capitals, and paragraph layout; verify it against the original.')
    steps.add_argument('--resume', action='store_true',
                       help='Check saved inputs, settings, and file checksums, then reuse completed steps.')
    chapters = parser.add_argument_group('Review and chapter drafts (require --author-workflow)')
    chapters.add_argument('--chapter-style', '--chapters', dest='chapters', choices=('none', 'interview', 'narrative', 'both'), default='none',
                          metavar='STYLE', help='Draft styles: interview excerpts, narrative arrangement, or both; includes review (default: none).')
    chapters.add_argument('--narrative-person', choices=('first', 'third'), default='first',
                          metavar='PERSON', help='Keep the source voice (first) or frame exact testimony in third person (default: first).')
    chapters.add_argument('--draft-with-unresolved-high', action='store_true',
                          help='Explicitly allow warned chapter drafts with unresolved high-priority review findings; incomplete review still blocks drafts.')
    transcription = parser.add_argument_group('Transcription models and hints')
    transcription.add_argument('--transcription-model', '--model', dest='model', default=DEFAULT_ASR_MODEL,
                               metavar='MODEL', help='Speech-to-text model for the original transcript (default: gpt-transcribe).')
    transcription.add_argument('--editing-model', default=DEFAULT_EDITING_MODEL,
                               metavar='MODEL', help='Model for optional punctuation and layout polish (default: gpt-6-astra).')
    transcription.add_argument('--review-model', '--author-model', dest='author_model', default=DEFAULT_EDITING_MODEL,
                               metavar='MODEL', help='Model for review and chapter arrangement (default: gpt-6-astra).')
    transcription.add_argument('--editing-reasoning-effort', choices=REASONING_EFFORTS, default='high',
                               metavar='EFFORT', help='Polish reasoning: low, medium, or high (default: high; balanced profile: low).')
    transcription.add_argument('--review-reasoning-effort', '--author-reasoning-effort', dest='review_reasoning_effort',
                               choices=REASONING_EFFORTS, default='high', metavar='EFFORT',
                               help='Review and narrative chapter reasoning: low, medium, or high (default: high; balanced profile: medium).')
    transcription.add_argument('--text-profile', choices=TEXT_PROFILES, default='legacy', metavar='PROFILE',
                               help='legacy: keep Astra/high defaults; balanced: Sol 6.1/low polish and Sol 6.1/medium review candidate. Explicit model/effort flags override each setting.')
    transcription.add_argument('--context-file', metavar='FILE', help='Private UTF-8 text file explaining the actual recording; sent to OpenAI.')
    transcription.add_argument('--glossary-file', metavar='FILE', help='Private UTF-8 list of expected terms, one per line; gpt-transcribe only.')
    transcription.add_argument('--language', action='append', default=[],
                               metavar='CODE', help='Expected lowercase language code, such as en; repeat for multilingual gpt-transcribe input.')
    transcription.add_argument('--audio-chunk-seconds', type=_positive_timeout, default=300,
                               metavar='SECONDS', help='Maximum audio seconds per transcription request, 1–600 (default: 300).')
    speakers = parser.add_argument_group('Speaker labels (require --author-workflow)')
    speakers.add_argument('--separate-speakers', '--interview', dest='interview', action='store_true',
                          help='Add a separate transcript with voice labels; names alone do not identify voices. Adds OpenAI requests.')
    speakers.add_argument('--interviewer-name', metavar='NAME', help='Display name for a confirmed interviewer; does not identify a voice.')
    speakers.add_argument('--interviewee-name', metavar='NAME', help='Display name for a confirmed interviewee; does not identify a voice.')
    speakers.add_argument('--speaker-map', action='append', default=[],
                        metavar='MAPPING', help='User-confirmed mapping PART:REQUEST:LABEL=interviewer or =interviewee; repeat. Unmapped speakers remain unidentified.')
    speakers.add_argument('--speaker-chunk-seconds', '--diarization-chunk-seconds', dest='diarization_chunk_seconds', type=_positive_timeout,
                          metavar='SECONDS', help='Audio seconds per voice-separation request, 1–600; defaults to the transcription chunk duration.')
    speakers.add_argument('--confirm-speaker-mappings', action='store_true',
                          help='Confirm supplied mappings were checked against the explicitly selected speaker chunks.')
    speakers.add_argument('--speaker-model', '--interview-model', dest='interview_model', default=DIARIZATION_MODEL,
                          metavar='MODEL', help='Voice-separation model (gpt-4o-transcribe-diarize); does not identify people. Original text uses --transcription-model.')
    execution = parser.add_argument_group('Progress, logs, and request limits')
    execution.add_argument('--progress', choices=('auto', 'plain', 'json'), default='auto',
                           metavar='MODE', help='auto: live terminal status, JSON when redirected; plain: readable scrolling lines; json: structured events (default: auto).')
    execution.add_argument('--logs-folder', '--log-directory', dest='log_directory',
                           metavar='FOLDER', help='Private structured execution logs (default: <output-folder>/execution-logs).')
    execution.add_argument('--status-interval', '--heartbeat-seconds', dest='heartbeat_seconds', type=_positive_timeout, default=30,
                           metavar='SECONDS', help='Seconds between idle status updates while a step is waiting (default: 30).')
    execution.add_argument('--media-timeout', type=_positive_timeout, default=3600,
                           metavar='SECONDS', help='Maximum seconds per local FFmpeg operation (default: 3600).')
    execution.add_argument('--request-timeout', '--provider-timeout', dest='provider_timeout', type=_positive_timeout, default=120,
                           metavar='SECONDS', help='Seconds allowed per OpenAI network wait (default: 120); limited by remaining run time when capped, not a whole-run deadline.')
    execution.add_argument('--request-retries', '--provider-retries', dest='provider_retries', type=int, choices=range(0, 6), default=2,
                           metavar='COUNT', help='OpenAI retries per operation, 0–5 (default: 2); nonzero also allows one eligible text-validation recovery. Retries can incur charges; use 0 with limits.')
    execution.add_argument('--max-requests', '--max-provider-requests', dest='max_provider_requests', type=int,
                           metavar='COUNT', help='Maximum new OpenAI operations across all steps; requires --request-retries 0. Use 1 for a limited diagnostic run.')
    execution.add_argument('--max-run-seconds', type=_positive_timeout,
                           metavar='SECONDS', help='Stop starting new requests after this many seconds; requires zero retries. Requests already running are not cancelled; omit for full runs.')
    execution.add_argument('--failure-limit', '--provider-failure-limit', dest='provider_failure_limit', type=int, default=2,
                           metavar='COUNT', help='Stop new requests after consecutive failures for one service/model (default: 2); account/configuration failures stop immediately.')
    execution.add_argument('--validation-failure-limit', type=int, default=3,
                           metavar='COUNT', help='Stop new requests after this many terminal text-validation failures in one stage/model across workers (default: 3); counts after recovery, never resets on SDK success.')
    compatibility = parser.add_argument_group('Compatibility')
    compatibility.add_argument('--format-as-interview', '--format_as_interview', dest='format_as_interview', action='store_true',
                               help='Legacy audio-folder layout option; never assigns speaker roles. Use --polish-text for new commands. Underscore spellings remain accepted.')
    return parser


def _execute(argv=None, *, approved_review=None, interview_options_override=None,
         approved_attributed_review=None, attributed_only=False, require_raw=False, studio=False):
    parser = transcription_parser(studio=studio)
    args = parser.parse_args(argv)
    supplied_options = parser.supplied_options(argv)
    if args.interview and (not args.workflow or args.extract_only):
        parser.usage_error('--interview requires --workflow and cannot use --extract-only.')
    if not args.interview and supplied_options & {'interviewer_name', 'interviewee_name', 'speaker_map', 'interview_model', 'diarization_chunk_seconds', 'confirm_speaker_mappings'}:
        parser.usage_error('Speaker naming options require --interview.')
    reporter = CURRENT.get()
    if reporter is not None:
        if not _in_batch(reporter) or 'progress' in supplied_options:
            reporter.set_output(args.progress)
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
        author_options_supplied = {'media_type', 'stages', 'chapters', 'narrative_person', 'author_model', 'review_reasoning_effort',
                                   'draft_with_unresolved_high'}
        if supplied_options & author_options_supplied:
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
                validation_failure_limit=args.validation_failure_limit,
                retries=args.provider_retries))
        if interview_options_override is not None and (type(interview_options_override) is not InterviewOptions
                or not args.workflow or args.interview_manifest is None or args.interview
                or supplied_options & {'interviewer_name', 'interviewee_name', 'speaker_map', 'interview_model'}):
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
                'text_profile': args.text_profile, 'editing_reasoning_effort': args.editing_reasoning_effort,
                'review_reasoning_effort': args.review_reasoning_effort,
                'diarization_model': interview_options.model if interview_options else DIARIZATION_MODEL,
                'audio_chunk_seconds': options.chunk_seconds,
                'diarization_chunk_seconds': (interview_options.diarization_chunk_seconds if interview_options else None) or options.chunk_seconds,
                'provider_timeout': args.provider_timeout, 'provider_retries': args.provider_retries,
                'max_provider_requests': args.max_provider_requests, 'max_run_seconds': args.max_run_seconds,
                'provider_failure_limit': args.provider_failure_limit,
                'validation_failure_limit': args.validation_failure_limit, 'interview': bool(interview_options),
                'context_supplied': bool(args.context_file), 'glossary_supplied': bool(args.glossary_file),
                'language_hint_count': len(options.languages)})
        if interview_options and (len(args.language) > 1 or any(len(code) != 2 for code in args.language)):
            raise ModelConfigurationError('Additional interview diarization accepts at most one ISO 639-1 language hint; omit hints for automatic detection.')
        editing_options = EditingOptions(model=args.editing_model, reasoning_effort=args.editing_reasoning_effort)
        if args.workflow:
            author_options = AuthorOptions(
                review='review' in requested or bool(styles),
                review_options=ReviewOptions(model=args.author_model, reasoning_effort=args.review_reasoning_effort),
                chapter_options=ChapterOptions(model=args.author_model, styles=styles,
                                               person=args.narrative_person,
                                               reasoning_effort=args.review_reasoning_effort) if styles else None,
                allow_unresolved_high=args.draft_with_unresolved_high,
            )
        if args.interview_manifest is not None:
            if reporter is not None and not _in_batch(reporter):
                reporter.context = {**reporter.context, 'selected': 1}
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
            _report(status='summary', processed=1, completed=1, failed=0, selected=1, finished=1)
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
        if reporter is not None and not _in_batch(reporter):
            reporter.context = {**reporter.context, 'selected': len(files)}
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
        if reporter is not None and not _in_batch(reporter):
            reporter.context = {**reporter.context, 'item': index}
        control = CURRENT_CONTROL.get()
        if control is not None and control.reason:
            _report(item=index, status='not_attempted', stop_reason=control.reason, finished=index)
            failures += 1
            unattempted += 1
            continue
        try:
            if pipeline:
                identity, stages = pipeline.process(item, transcriber=transcriber,
                                                    extract_only=args.extract_only,
                                                    enhance=args.enhance_for_reading or (args.workflow and 'polish' in requested))
                _report(item=index, job=identity, status='complete', stages=stages, finished=index)
            else:
                stages = _legacy_process(item, output, transcriber, args)
                _report(item=index, status='complete', stages=stages, finished=index)
        except Exception as error:
            failures += 1
            message = str(error) if type(error) in (MediaError, PipelineError, TranscriptionError) else 'Processing failed; check media validity, output access, and free space.'
            control = CURRENT_CONTROL.get()
            limited = control is not None and control.reason in {'request_limit', 'start_deadline'}
            incomplete += int(limited)
            _report(item=index, status='incomplete' if limited else 'failed', message=message,
                    finished=index,
                    stop_reason=control.reason if control else None,
                    stages=error.stages if type(error) is PipelineError else {})
    if reporter is not None and not _in_batch(reporter):
        reporter.context = {key: value for key, value in reporter.context.items() if key != 'item'}
    _report(status='summary', processed=len(files) - unattempted, failed=failures - incomplete - unattempted,
            completed=len(files) - failures, incomplete=incomplete, not_attempted=unattempted,
            selected=len(files), finished=len(files))
    return 1 if failures else 0


def main(argv=None, *, approved_review=None, interview_options_override=None,
         approved_attributed_review=None, attributed_only=False, require_raw=False, studio=False):
    token = CURRENT_CONTROL.set(CURRENT_CONTROL.get())
    try:
        return _execute(argv, approved_review=approved_review,
            interview_options_override=interview_options_override,
            approved_attributed_review=approved_attributed_review, attributed_only=attributed_only,
            require_raw=require_raw, studio=studio)
    finally:
        CURRENT_CONTROL.reset(token)


def entrypoint(argv=None, *, studio=False):
    reporter = Reporter(sys.stdout, output='auto')
    reporter.context = {'scope': 'interview'}
    token = CURRENT.set(reporter)
    control_token = CURRENT_CONTROL.set(None)
    try:
        with interruptions():
            code = main(argv, studio=studio)
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
