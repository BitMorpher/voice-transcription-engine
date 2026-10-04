"""Explicit local recording order, verified part caches, and one author workflow."""

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

if __package__:
    from . import transcriber as asr_engine
    from .author_review import source_segments
    from .author_workflow import (
        AuthorWorkflowError,
        _load_bound_report,
        _verified_bundle,
        run_author_stages,
    )
    from .media import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, VIDEO_EXTENSIONS
    from .pipeline import Pipeline, PipelineError
    from .private_output import digest, output_directory, write_private
    from .source_provenance import author_binding, validate_chapter_binding
else:
    import transcriber as asr_engine
    from author_review import source_segments
    from author_workflow import (
        AuthorWorkflowError,
        _load_bound_report,
        _verified_bundle,
        run_author_stages,
    )
    from media import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, VIDEO_EXTENSIONS
    from pipeline import Pipeline, PipelineError
    from private_output import digest, output_directory, write_private
    from source_provenance import author_binding, validate_chapter_binding


CONTRACT = 1
SEPARATOR = "\n\n"
SAFE_INPUT = (
    "Invalid interview manifest; use version 1 with one interview_id and a nonempty "
    "ordered parts array of unique IDs and accessible nonempty local media files."
)
SAFE_CACHE = "Interview cache is invalid, changed, locked, or conflicts; use a new output folder. No artifact was overwritten."


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _read_json(path):
    if path.is_symlink():
        raise ValueError()
    return json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=_object)


def _local(path):
    # resolve permits ordinary relative ../ references but never symlink aliases.
    absolute = Path(os.path.abspath(path))
    for ancestor in (absolute, *absolute.parents):
        if ancestor.is_symlink() and str(ancestor) not in {"/tmp", "/var"}:
            raise ValueError()
    return absolute.resolve(strict=True)


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value)


def load_interview(path):
    """Strict versioned schema; the array is the only authority for part order."""
    try:
        if not isinstance(path, (str, Path)) or not str(path):
            raise ValueError()
        manifest = _local(path)
        document = _read_json(manifest)
        if (
            not isinstance(document, dict)
            or set(document) != {"version", "interview_id", "parts"}
            or type(document["version"]) is not int
            or document["version"] != CONTRACT
            or not _identifier(document["interview_id"])
            or not isinstance(document["parts"], list)
            or not document["parts"]
        ):
            raise ValueError()
        ids, files, parts = set(), set(), []
        for index, part in enumerate(document["parts"], 1):
            if (
                not isinstance(part, dict)
                or not {"id", "path"} <= set(part)
                or set(part) - {"id", "path", "media_type"}
                or not _identifier(part["id"])
                or part["id"] in ids
                or not isinstance(part["path"], str)
                or not part["path"]
                or "://" in part["path"]
                or part["path"].startswith("file:")
            ):
                raise ValueError()
            media_type = part.get("media_type", "auto")
            if media_type not in ("auto", "audio", "video"):
                raise ValueError()
            source = _local(manifest.parent / part["path"])
            stat = source.stat()
            file_id = (stat.st_dev, stat.st_ino)
            allowed = {
                "auto": MEDIA_EXTENSIONS,
                "audio": AUDIO_EXTENSIONS,
                "video": VIDEO_EXTENSIONS,
            }[media_type]
            if (
                not source.is_file()
                or not stat.st_size
                or source.suffix.lower() not in allowed
                or file_id in files
            ):
                raise ValueError()
            ids.add(part["id"])
            files.add(file_id)
            parts.append(
                {
                    "id": part["id"],
                    "order": index,
                    "path": str(source),
                    "media_type": media_type,
                    "source_sha256": digest(source),
                }
            )
        return {"version": CONTRACT, "interview_id": document["interview_id"], "parts": parts}
    except OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError:
        raise PipelineError(SAFE_INPUT) from None


class OrderedInterview:
    def __init__(
        self,
        manifest,
        output,
        *,
        options,
        editing_options,
        author_options,
        resume=False,
        media_timeout=3600,
        enhance=False,
        progress=None,
    ):
        self.document = load_interview(manifest)
        self.output = output_directory(output)
        self.options, self.editing_options = options, editing_options
        self.author_options = author_options
        self.resume, self.media_timeout, self.enhance = resume, media_timeout, enhance
        self.progress = progress or (lambda stage, status: None)
        self.binding = _hash(
            {
                "contract": CONTRACT,
                "manifest": self.document,
                "asr": options.fingerprint,
                "editing": editing_options.fingerprint,
                "editing_prompt": _hash(
                    {"instruction": asr_engine.FAITHFUL_INSTRUCTION, "schema": asr_engine.schema(1)}
                ),
                "review": author_options.review_options.fingerprint,
                "chapters": (
                    author_options.chapter_options.fingerprint
                    if author_options.chapter_options
                    else None
                ),
                "allow_unresolved_high": author_options.allow_unresolved_high,
            }
        )
        self.job = output_directory(self.output / "interviews") / self.binding
        output_directory(self.output / "parts")
        self.parts = []
        for part in self.document["parts"]:
            cache = _hash(
                {
                    "path": part["path"],
                    "source": part["source_sha256"],
                    "asr": options.fingerprint,
                    "contract": CONTRACT,
                }
            )
            source = Path(part["path"])
            identity = hashlib.sha256(
                os.fsencode(source.name) + b"\0" + bytes.fromhex(part["source_sha256"])
            ).hexdigest()
            directory = self.output / "parts" / cache
            self.parts.append((part, directory, identity))

    def _combine(self):
        text, spans, records, cursor = [], [], [], 0
        for part, directory, identity in self.parts:
            job = directory / identity
            state = _read_json(job / "manifest.json")
            if not Pipeline._verified(
                job, state, "transcription", "transcription.txt", self.options.fingerprint
            ):
                raise ValueError()
            # read_bytes preserves CRLF and every Unicode character in provider output.
            raw = (job / "transcription.txt").read_bytes().decode("utf-8")
            if (
                not raw.strip()
                or hashlib.sha256(raw.encode()).hexdigest()
                != state["stages"]["transcription"]["sha256"]
            ):
                raise ValueError()
            if text:
                spans.append({"kind": "separator", "start": cursor, "end": cursor + len(SEPARATOR)})
                text.append(SEPARATOR)
                cursor += len(SEPARATOR)
            start = cursor
            text.append(raw)
            cursor += len(raw)
            spans.append(
                {
                    "kind": "recording",
                    "part_id": part["id"],
                    "order": part["order"],
                    "start": start,
                    "end": cursor,
                }
            )
            records.append(
                {
                    **part,
                    "start": start,
                    "end": cursor,
                    "raw_transcript": str((job / "transcription.txt").relative_to(self.output)),
                    "raw_sha256": state["stages"]["transcription"]["sha256"],
                    "segments": [
                        {k: v for k, v in segment.items() if k != "text"}
                        for segment in source_segments(raw)
                    ],
                }
            )
        raw = "".join(text)
        provenance = {
            "version": CONTRACT,
            "interview_id": self.document["interview_id"],
            "manifest_sha256": _hash(self.document),
            "human_review_required": True,
            "offset_unit": "Python Unicode characters; zero based; end exclusive",
            "recording_time": "Unavailable; local text offsets are not audio timestamps.",
            "separator": SEPARATOR,
            "parts": records,
            "spans": spans,
            "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        }
        return raw, provenance

    def preflight(self):
        """Check every existing cache/output before any provider request."""
        try:
            for part, directory, identity in self.parts:
                source = _local(part["path"])
                if digest(source) != part["source_sha256"]:
                    raise ValueError()
                job = directory / identity
                for ancestor in (directory, job, *directory.parents):
                    if ancestor.is_symlink():
                        raise ValueError()
                if not job.exists():
                    continue
                if not self.resume or os.path.lexists(job / ".lock"):
                    raise ValueError()
                state = _read_json(job / "manifest.json")
                if (
                    state["version"] != 2
                    or state["source_sha256"] != part["source_sha256"]
                    or state["transcription_configuration_sha256"] != self.options.fingerprint
                    or state["model"] != self.options.model
                    or not isinstance(state["stages"], dict)
                ):
                    raise ValueError()
                for stage, name in [
                    ("conversion", "audio.wav"),
                    ("transcription", "transcription.txt"),
                ]:
                    record = state["stages"].get(stage)
                    if os.path.lexists(job / name) or (
                        isinstance(record, dict) and record.get("status") == "complete"
                    ):
                        if not Pipeline._verified(
                            job,
                            state,
                            stage,
                            name,
                            self.options.fingerprint if stage == "transcription" else None,
                        ):
                            raise ValueError()
                        if (
                            stage == "transcription"
                            and not (job / name).read_bytes().decode("utf-8").strip()
                        ):
                            raise ValueError()
            if self.job.parent.is_symlink() or self.job.is_symlink():
                raise ValueError()
            if self.job.exists():
                if not self.resume:
                    raise ValueError()
                state = _read_json(self.job / "manifest.json")
                if (
                    state["version"] != "ordered-interview-v1"
                    or state["binding_sha256"] != self.binding
                ):
                    raise ValueError()
                raw, provenance = self._combine()
                if (
                    (self.job / "transcription.txt").is_symlink()
                    or (self.job / "transcription.txt").read_bytes() != raw.encode("utf-8")
                    or _read_json(self.job / "provenance.json") != provenance
                    or not Pipeline._verified(self.job, state, "transcription", "transcription.txt")
                    or state["provenance_sha256"] != digest(self.job / "provenance.json")
                    or (self.job / "part_boundaries.txt").is_symlink()
                    or state["boundaries_sha256"] != digest(self.job / "part_boundaries.txt")
                ):
                    raise ValueError()
                self._preflight_author(state, raw, provenance)
        except OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError:
            raise PipelineError(SAFE_CACHE) from None

    def _preflight_author(self, state, raw, provenance):
        pipeline = Pipeline(self.output, options=self.options, editing_options=self.editing_options)
        record = state["stages"].get("enhancement")
        if self.enhance and (record or os.path.lexists(self.job / "derivative_readability.txt")):
            if not Pipeline._verified(
                self.job,
                state,
                "enhancement",
                "derivative_readability.txt",
                pipeline._enhancement_fingerprint(state["stages"]["transcription"]["sha256"]),
                state["stages"]["transcription"]["sha256"],
            ):
                # Failed attempts with no output are safe to retry.
                if (
                    os.path.lexists(self.job / "derivative_readability.txt")
                    or record.get("status") == "complete"
                ):
                    raise ValueError()
        review_hash = None
        for stage in ("author_review", "chapters"):
            record = state["stages"].get(stage)
            requested = (
                self.author_options.review
                if stage == "author_review"
                else self.author_options.chapter_options is not None
            )
            if not requested or not record or record.get("status") != "complete":
                continue
            names = (
                {"review_report.json", "review_report.xlsx"}
                if stage == "author_review"
                else {
                    "chapter_drafts.json",
                    *(
                        f"chapter_{style}.txt"
                        for style in self.author_options.chapter_options.styles
                    ),
                }
            )
            raw_hash = state["stages"]["transcription"]["sha256"]
            config = author_binding(
                self.author_options.fingerprint(stage, raw_hash, review_hash), provenance
            )
            if not _verified_bundle(self.job, record, config, raw_hash, names):
                raise ValueError()
            if stage == "chapters":
                name = next(
                    name for name in record["artifacts"] if name.endswith("/chapter_drafts.json")
                )
                validate_chapter_binding(_read_json(self.job / name), provenance)
            if stage == "author_review":
                _, review_hash = _load_bound_report(
                    self.job, record, raw, self.author_options.review_options, provenance=provenance
                )

    def process(self, *, transcriber):
        """Publish combined raw only after every part succeeds, then run author stages."""
        original_resume = self.resume
        lock = self.output / ".interview.lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            raise PipelineError(SAFE_CACHE) from None
        try:
            self.preflight()
            existed = self.job.exists()
            # Initial validation enforces the caller's resume choice. Internal passes
            # can now reuse artifacts created during this same locked run.
            self.resume = True
            if (
                transcriber.options.fingerprint != self.options.fingerprint
                or transcriber.editing_options.fingerprint != self.editing_options.fingerprint
            ):
                raise PipelineError("Transcriber settings must match the interview configuration.")
            # Validate/decode ALL parts locally before ASR, including later recordings.
            for part, directory, identity in self.parts:
                pipeline = Pipeline(
                    directory,
                    resume=self.resume,
                    media_timeout=self.media_timeout,
                    options=self.options,
                )
                pipeline.process(part["path"], extract_only=True)
            self.preflight()
            for part, directory, identity in self.parts:
                self.progress("part_transcription", "running")
                Pipeline(
                    directory, resume=True, media_timeout=self.media_timeout, options=self.options
                ).process(part["path"], transcriber=transcriber, require_nonempty=True)
                self.progress("part_transcription", "complete")
            self.preflight()
            raw, provenance = self._combine()
            if not self.job.exists():
                self._publish(raw, provenance)
            state = _read_json(self.job / "manifest.json")
            summary = {"parts": "complete", "combined_raw": "skipped" if existed else "complete"}

            def save():
                Pipeline._save(self.job / "manifest.json", state)

            if self.enhance:
                raw_hash = state["stages"]["transcription"]["sha256"]
                pipeline = Pipeline(
                    self.output, options=self.options, editing_options=self.editing_options
                )
                config = pipeline._enhancement_fingerprint(raw_hash)
                if Pipeline._verified(
                    self.job, state, "enhancement", "derivative_readability.txt", config, raw_hash
                ):
                    summary["enhancement"] = "skipped"
                else:
                    Pipeline._write_derivative(self.job, transcriber, raw_hash)
                    state["stages"]["enhancement"] = {
                        "status": "complete",
                        "sha256": digest(self.job / "derivative_readability.txt"),
                        "configuration_sha256": config,
                        "transcription_sha256": raw_hash,
                    }
                    save()
                    summary["enhancement"] = "complete"
            try:
                run_author_stages(
                    self.job,
                    state,
                    transcriber,
                    self.author_options,
                    resume=self.resume,
                    save=save,
                    summary=summary,
                    progress=self.progress,
                    provenance=provenance,
                )
            except AuthorWorkflowError as error:
                raise PipelineError(str(error), stages=summary) from None
            return self.binding, summary
        except PipelineError:
            raise
        except Exception:
            raise PipelineError(
                "Interview processing failed; completed part outputs are retained. Check local media and output access before resuming."
            ) from None
        finally:
            self.resume = original_resume
            lock.unlink()

    def _publish(self, raw, provenance):
        parent = output_directory(self.job.parent)
        temporary = Path(tempfile.mkdtemp(prefix=".interview-", dir=parent))
        try:
            write_private(temporary / "transcription.txt", raw)
            write_private(
                temporary / "provenance.json",
                json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
            )
            boundaries = [
                "AUTOMATIC UNVERIFIED TRANSCRIPT — listen to every recording.",
                "Parts continue one interview. Pauses imply no missing content.",
                "Combined offsets are characters, never global audio time.\n",
            ]
            boundaries.extend(
                f"Part {p['order']:06d} ({p['id']}): combined characters {p['start']}:{p['end']}; "
                f"local raw: {p['raw_transcript']}"
                for p in provenance["parts"]
            )
            write_private(temporary / "part_boundaries.txt", "\n".join(boundaries) + "\n")
            state = {
                "version": "ordered-interview-v1",
                "binding_sha256": self.binding,
                "provenance_sha256": digest(temporary / "provenance.json"),
                "boundaries_sha256": digest(temporary / "part_boundaries.txt"),
                "human_review_required": True,
                "stages": {
                    "transcription": {"status": "complete", "sha256": provenance["raw_sha256"]}
                },
            }
            Pipeline._save(temporary / "manifest.json", state)
            if os.path.lexists(self.job):
                raise PipelineError(SAFE_CACHE)
            os.rename(temporary, self.job)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
