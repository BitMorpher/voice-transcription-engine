"""Local character provenance for ordered recordings; never infer recording time."""

import copy
import hashlib
import json

if __package__:
    from .author_review import source_segments
else:
    from author_review import source_segments


def references(provenance, start, end):
    """Intersect a combined character span with part text and explicit separators."""
    result = []
    parts = {part["id"]: part for part in provenance["parts"]}
    for span in provenance["spans"]:
        a, b = max(start, span["start"]), min(end, span["end"])
        if a >= b:
            continue
        reference = {"kind": span["kind"], "start": a, "end": b}
        if span["kind"] == "recording":
            reference.update(
                part_id=span["part_id"],
                order=span["order"],
                local_start=a - span["start"],
                local_end=b - span["start"],
                local_segment_ids=[
                    segment["segment_id"]
                    for segment in parts[span["part_id"]]["segments"]
                    if segment["start"] < b - span["start"] and segment["end"] > a - span["start"]
                ],
            )
        if 'attribution' in provenance:
            reference['speaker_turns'] = [
                {key: turn[key] for key in ('turn_id', 'speaker_key', 'display_name', 'role',
                    'identity_evidence', 'diarization_evidence', 'audio_start', 'audio_end',
                    'overlap_detected', 'speech_start', 'speech_end', 'cache_binding_sha256',
                    'provider_response_sha256')}
                for turn in provenance['attribution']['turns']
                if turn['start'] < b and turn['end'] > a
            ]
        result.append(reference)
    return result


def bind_report(report, provenance):
    """Keep standard raw segments intact; attach deterministic recording references."""
    report["recording_provenance"] = copy.deepcopy(provenance)
    report["recording_segment_refs"] = {
        segment["segment_id"]: references(provenance, segment["start"], segment["end"])
        for segment in report["segments"]
    }
    for finding in report["findings"]:
        finding["recording_refs"] = references(provenance, finding["start"], finding["end"])


def validate_binding(report, provenance):
    expected = copy.deepcopy(report)
    bind_report(expected, provenance)
    if report != expected:
        raise ValueError("Invalid recording provenance binding.")


def bind_chapters(document, provenance):
    document["recording_provenance"] = copy.deepcopy(provenance)
    for finding in document["findings"]:
        finding["recording_refs"] = references(provenance, finding["start"], finding["end"])
    for chapter in document["chapters"].values():
        if 'attribution' in provenance:
            warning = provenance['attribution']['notice'].strip()
            if warning not in chapter['warnings']:
                chapter['warnings'].append(warning)
        for item in [*chapter["passages"], *chapter["coverage_omissions"]]:
            item["recording_refs"] = references(provenance, item["start"], item["end"])
            if 'attribution' in provenance and 'passage_id' in item:
                item['attribution'] = 'Source speaker metadata retained; names are user-confirmed mappings; diarization remains unverified.'
            if "quote_start" in item:
                item["quote_recording_refs"] = references(
                    provenance, item["quote_start"], item["quote_end"]
                )


def fingerprint(provenance):
    return hashlib.sha256(
        json.dumps(provenance, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def author_binding(configuration, provenance):
    return hashlib.sha256(
        json.dumps(
            {"author": configuration, "provenance": fingerprint(provenance), "contract": 1},
            sort_keys=True,
        ).encode()
    ).hexdigest()


def validate_provenance(raw, provenance):
    """Check full partition coverage and part transcript hashes, without reading media."""
    if (
        provenance["version"] != 1
        or provenance["human_review_required"] is not True
        or provenance["separator"] != "\n\n"
        or provenance["raw_sha256"] != hashlib.sha256(raw.encode()).hexdigest()
        or not provenance["parts"]
    ):
        raise ValueError()
    expected, ids, cursor = [], set(), 0
    for order, part in enumerate(provenance["parts"], 1):
        if order > 1:
            expected.append({"kind": "separator", "start": cursor, "end": cursor + 2})
            if raw[cursor : cursor + 2] != "\n\n":
                raise ValueError()
            cursor += 2
        start, end = part["start"], part["end"]
        if (
            type(start) is not int
            or type(end) is not int
            or start != cursor
            or not start < end <= len(raw)
            or part["order"] != order
            or not isinstance(part["id"], str)
            or not part["id"]
            or part["id"] in ids
            or not raw[start:end].strip()
            or part["raw_sha256"] != hashlib.sha256(raw[start:end].encode()).hexdigest()
            or part["segments"]
            != [
                {k: v for k, v in segment.items() if k != "text"}
                for segment in source_segments(raw[start:end])
            ]
        ):
            raise ValueError()
        ids.add(part["id"])
        expected.append(
            {"kind": "recording", "part_id": part["id"], "order": order, "start": start, "end": end}
        )
        cursor = end
    if cursor != len(raw) or provenance["spans"] != expected:
        raise ValueError()
    if 'attribution' in provenance:
        attribution = provenance['attribution']
        if attribution['contract'] not in (1, 2) or attribution['names_are_not_voice_evidence'] is not True:
            raise ValueError()
        previous = 0
        for turn in attribution['turns']:
            if (not previous <= turn['start'] < turn['speech_start'] <= turn['speech_end'] < turn['end'] <= len(raw)
                    or turn['identity_evidence'] not in ('user_confirmed_mapping', 'unidentified')
                    or turn['diarization_evidence'] != 'provider_automatic_unverified'
                    or (turn['role'] is None) != (turn['identity_evidence'] == 'unidentified')
                    or turn['speech_sha256'] != hashlib.sha256(raw[turn['speech_start']:turn['speech_end']].encode()).hexdigest()):
                raise ValueError()
            previous = turn['end']


def validate_chapter_binding(document, provenance):
    expected = copy.deepcopy(document)
    bind_chapters(expected, provenance)
    if document != expected or document["raw_sha256"] != provenance["raw_sha256"]:
        raise ValueError("Invalid chapter recording provenance binding.")
