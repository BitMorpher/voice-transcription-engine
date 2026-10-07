"""Build/install the wheel and exercise entrypoints outside every source checkout."""

import json
import os
from pathlib import Path
import shutil
import site
import subprocess
import sys
import zipfile

import pytest


@pytest.fixture(scope='module')
def wheel_environment(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    work = tmp_path_factory.mktemp('wheel-smoke')
    uv = shutil.which('uv')
    assert uv, 'uv is a documented development prerequisite.'
    environment = dict(os.environ)
    result = subprocess.run([uv, 'build', '--offline', '--out-dir', str(work / 'dist')],
                            cwd=root, env=environment, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, 'Offline wheel/source build failed; run uv build after installing build requirements.'
    wheel = next((work / 'dist').glob('*.whl'))
    runtime = work / 'runtime'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(runtime)], check=True,
                   capture_output=True, timeout=60)
    python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    result = subprocess.run([uv, 'pip', 'install', '--offline', '--python', str(python), '--no-deps', str(wheel)],
                            env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, 'Offline wheel installation failed.'
    # Borrow only locked runtime dependencies. Prioritize this wheel's physical
    # site-packages so a noneditable test installation cannot shadow the wheel;
    # PYTHONPATH does not execute the test environment's editable .pth files.
    result = subprocess.run([str(python), '-c',
                             'import sysconfig; print(sysconfig.get_path("purelib"))'],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    environment['PYTHONPATH'] = os.pathsep.join([result.stdout.strip(), *site.getsitepackages()])
    environment.pop('OPENAI_API_KEY', None)
    return work, python, environment, wheel


def test_wheel_namespace_prompts_and_console_scripts(wheel_environment):
    work, python, environment, wheel = wheel_environment
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert 'voice_transcription_engine/cli.py' in names
        assert 'voice_transcription_engine/studio_cli.py' in names
        assert 'voice_transcription_engine/batch/runner.py' in names
        assert 'voice_transcription_engine/batch/concurrency.py' in names
        assert any(name.startswith('voice_transcription_engine/prompts/') and name.endswith('.txt') for name in names)
        assert 'cli.py' not in names
        assert not any('private/' in name or '.env' in name or 'batch-plan' in name for name in names)
        metadata = archive.read('interview_studio-0.1.0.dist-info/METADATA').decode()
        assert 'Name: interview-studio\n' in metadata
        assert 'https://github.com/BitMorpher/interview-studio' in metadata
        scripts = archive.read('interview_studio-0.1.0.dist-info/entry_points.txt').decode()
        for declaration in ('interview = voice_transcription_engine.studio_cli:main',
                            'voice-transcribe = voice_transcription_engine.cli:entrypoint',
                            'voice-batch = voice_transcription_engine.batch.cli:main'):
            assert declaration in scripts
    result = subprocess.run([str(python), '-c',
        'import voice_transcription_engine.cli as c; print(c.__file__)'],
        cwd=work, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert str(work / 'runtime') in result.stdout
    for command, arguments in (('voice-transcribe', []), ('voice-batch', []),
                               ('interview', []), ('interview', ['transcribe']),
                               ('interview', ['batch']), ('interview', ['compare-text'])):
        executable = python.parent / (command + '.exe' if os.name == 'nt' else command)
        result = subprocess.run([str(executable), *arguments, '--help'], cwd=work, env=environment,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and command in result.stdout
        assert result.stderr == ''


def test_installed_wheel_synthetic_comparison_and_replay(wheel_environment):
    work, python, environment, _ = wheel_environment
    executable = python.parent / ('interview.exe' if os.name == 'nt' else 'interview')
    output = work / 'synthetic-comparison'
    arguments = [str(executable), 'compare-text', '--output-dir', str(output)]
    first = subprocess.run(arguments, cwd=work, env=environment,
                           capture_output=True, text=True, timeout=30)
    assert first.returncode == 0, first.stderr
    results = list(output.rglob('completed.json'))
    assert results
    snapshots = {path: path.read_bytes() for path in results}
    replay = subprocess.run([*arguments, '--resume'], cwd=work, env=environment,
                            capture_output=True, text=True, timeout=30)
    assert replay.returncode == 0, replay.stderr
    assert all(path.read_bytes() == data for path, data in snapshots.items())


def test_wheel_synthetic_pipeline_and_resume(wheel_environment):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg/ffprobe required for installed media smoke.')
    work, python, environment, _ = wheel_environment
    script = work / 'smoke.py'
    script.write_text('''
import io, json, socket, wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import voice_transcription_engine.cli as engine
from voice_transcription_engine.batch.cli import main as batch
from voice_transcription_engine.studio_cli import main as interview
from voice_transcription_engine.transcriber import Transcriber
from importlib.resources import files

def blocked(*a, **kw):
    raise AssertionError('No real network allowed.')
socket.socket.connect = blocked
assert files('voice_transcription_engine.prompts').joinpath('author_review_v1.txt').is_file()
source = Path('synthetic.wav')
with wave.open(str(source), 'wb') as audio:
    audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
    audio.writeframes(b'\\0\\0' * 2000)
client = MagicMock()
client.audio.transcriptions.create.return_value = SimpleNamespace(text='Synthetic testimony.\\r\\nExact tail.')
def response(**kwargs):
    p = json.loads(kwargs['messages'][-1]['content'])
    name = kwargs['response_format']['json_schema']['name']
    if name == 'faithful_transcript_edit':
        body = dict(chunk_index=p['chunk_index'], text=p['text'], speaker_uncertain=False)
    elif name == 'faithful_turn_group_edit':
        body = dict(group_index=p['group_index'], edits=[{**turn, 'speaker_uncertain': False} for turn in p['turns']])
    elif name == 'source_grounded_author_review':
        p['text'] = ''.join(p['text'] for p in p['evidence_pieces'])
        body = dict(chunk_index=p['chunk_index'], fully_reviewed=True, contract_version=p['contract_version'],
                    reviewed_piece_ids=p['core_piece_ids'], findings=[])
    else:
        body = dict(chunk_index=p['chunk_index'], passages=[dict(unit_ids=[u['unit_id']],
                    text=u['text'].strip(), kind='verbatim_excerpt') for u in p['source_units']], coverage_omissions=[])
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
        message=SimpleNamespace(content=json.dumps(body), refusal=None))])
client.chat.completions.create.side_effect = response
engine.Transcriber = lambda **kw: Transcriber(client=client, **kw)
Path('interview.json').write_text(json.dumps(dict(version=1, interview_id='session-a',
    parts=[dict(id='part-a', path=source.name)])))
Path('plan.json').write_text(json.dumps(dict(version=1,
    interviews=[dict(id='entry-a', manifest='interview.json')])))
assert batch(['prepare', '--plan', 'plan.json', '--batch', 'batch', '--copy-local-files']) == 0
for phase in ('raw', 'review', 'chapters'):
    args = ['run', '--batch', 'batch', '--phase', phase, '--send-to-openai']
    if phase == 'chapters':
        reviewed_reports = {path.read_bytes() for path in Path('batch').rglob('review_report.json')}
        reviewed_workbooks = {path.read_bytes() for path in Path('batch').rglob('review_report.xlsx')}
        args += ['--select', 'entry-a', '--human-reviewed']
    assert batch(args) == 0
calls = client.chat.completions.create.call_count
assert interview(['batch', *args]) == 0
assert client.chat.completions.create.call_count == calls
assert client.audio.transcriptions.create.call_count == 1
assert interview(['batch', 'verify', '--batch', 'batch']) == 0
assert sum(call.kwargs['response_format']['json_schema']['name'] == 'source_grounded_author_review'
           for call in client.chat.completions.create.call_args_list) == 1
assert {path.read_bytes() for path in Path('batch').rglob('review_report.json')} == reviewed_reports
assert {path.read_bytes() for path in Path('batch').rglob('review_report.xlsx')} == reviewed_workbooks
assert len(list(Path('batch').rglob('chapter_drafts.json'))) == 1
''')
    result = subprocess.run([str(python), str(script)], cwd=work, env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, 'Installed wheel synthetic workflow/resume smoke failed.'
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert any(row.get('cache_reused') for row in rows)
    assert any(row.get('stage') == 'author_review' and row.get('chunk') == 1 for row in rows)
    assert str(work) not in result.stdout and 'Synthetic testimony' not in result.stdout
    assert result.stderr == ''


def test_wheel_parallel_interviews_shared_allowance_and_aliases(wheel_environment):
    work, python, environment, _ = wheel_environment
    work = work / 'parallel-smoke'
    work.mkdir()
    script = work / 'parallel.py'
    script.write_text('''
import contextlib, io, json, shutil, socket, threading, wave
from pathlib import Path
from types import SimpleNamespace
import voice_transcription_engine.cli as engine
import voice_transcription_engine.pipeline as pipeline
from voice_transcription_engine.batch.cli import main as batch
from voice_transcription_engine.progress import CURRENT
from voice_transcription_engine.transcriber import Transcriber

def blocked(*args, **kwargs):
    raise AssertionError('Synthetic providers only; no network.')
socket.socket.connect = blocked
pipeline.prepare_audio = lambda source, target, **kwargs: shutil.copyfile(source, target)
engine.require_ffmpeg = lambda: None
entries = []
for item in range(1, 4):
    source = Path(f'synthetic-{item}.wav')
    with wave.open(str(source), 'wb') as wav:
        wav.setparams((1, 2, 100, 0, 'NONE', 'not compressed'))
        wav.writeframes(item.to_bytes(2, 'little') * 120)
    manifest = Path(f'ordered-{item}.json')
    manifest.write_text(json.dumps(dict(version=1, interview_id=f'synthetic-{item}',
        parts=[dict(id='one', path=source.name, media_type='audio')])))
    entries.append(dict(id=f'entry-{item}', manifest=manifest.name))
Path('plan.json').write_text(json.dumps(dict(version=1, interviews=entries)))
calls, barrier, mutex = [], threading.Barrier(2), threading.Lock()
def response(**parameters):
    with mutex:
        calls.append((CURRENT.get().context['item'], parameters['model']))
        ordinal = len(calls)
    if ordinal <= 2:
        barrier.wait(timeout=5)
    return SimpleNamespace(text='SYNTHETIC_PRIVATE_WORDS')
provider = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=response)))
engine.Transcriber = lambda **kwargs: Transcriber(client=provider, **kwargs)
output = io.StringIO()
with contextlib.redirect_stdout(output):
    assert batch(['prepare', '--plan', 'plan.json', '--batch', 'batch', '--copy-local-files']) == 0
    args = ['run', '--batch', 'batch', '--send-to-openai', '--parallel-interviews', '2',
            '--transcription-model', 'gpt-transcribe', '--audio-chunk-seconds', '1', '--provider-retries', '0']
    assert batch(args + ['--max-provider-requests', '2']) == 1
    assert len(calls) == 2
    assert len(list(Path('batch').rglob('response.json'))) == 2
    assert batch(args) == 0
    assert len(calls) == 6
    before = {p: p.read_bytes() for p in Path('batch').rglob('*') if p.is_file() and 'output' in p.parts}
    assert batch(args) == 0 and len(calls) == 6
    assert all(p.read_bytes() == data for p, data in before.items())
    assert batch(['status', '--batch', 'batch']) == 0
assert not list(Path('batch').rglob('*.lock'))
assert 'SYNTHETIC_PRIVATE_WORDS' not in output.getvalue() and str(Path.cwd()) not in output.getvalue()
rows = [json.loads(line) for line in output.getvalue().splitlines()]
assert any(row.get('configuration', {}).get('parallel_interviews') == 2 for row in rows)
''')
    result = subprocess.run([str(python), str(script)], cwd=work, env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, 'Installed wheel parallel/recovery smoke failed.'
    assert result.stderr == ''


def test_wheel_interview_family_and_private_cli(wheel_environment):
    work, python, environment, _ = wheel_environment
    script = work / 'interview-smoke.py'
    script.write_text('''
import json, socket, wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import voice_transcription_engine.cli as engine
from voice_transcription_engine.transcriber import Transcriber
from voice_transcription_engine.interview_attribution import DIARIZATION_MODEL
from voice_transcription_engine.studio_cli import main as interview

def blocked(*a, **kw):
    raise AssertionError('No real network allowed.')
socket.socket.connect = blocked
source = Path('interview-synthetic.wav')
with wave.open(str(source), 'wb') as wav:
    wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
    wav.writeframes(b'\\0\\0' * 36000)
client = MagicMock()
def audio(**kw):
    if kw['model'] == DIARIZATION_MODEL:
        assert set(kw) == {'model', 'response_format', 'chunking_strategy', 'file'}
        return {'text': 'A: Synthetic words.', 'segments': [
            {'speaker': 'A', 'start': 0, 'end': 0.1, 'text': 'Synthetic words.'}]}
    return SimpleNamespace(text='Original words.\\r\\nExact tail.')
client.audio.transcriptions.create.side_effect = audio
def chat(**kw):
    supplied = json.loads(kw['messages'][-1]['content'])
    name = kw['response_format']['json_schema']['name']
    if name == 'faithful_transcript_edit':
        body = dict(chunk_index=supplied['chunk_index'], text=supplied['text'], speaker_uncertain=False)
    elif name == 'faithful_turn_group_edit':
        body = dict(group_index=supplied['group_index'], edits=[{**turn, 'speaker_uncertain': False} for turn in supplied['turns']])
    elif name == 'source_grounded_author_review':
        supplied['text'] = ''.join(p['text'] for p in supplied['evidence_pieces'])
        body = dict(chunk_index=supplied['chunk_index'], fully_reviewed=True,
                    contract_version=supplied['contract_version'], reviewed_piece_ids=supplied['core_piece_ids'], findings=[])
    else:
        body = dict(chunk_index=supplied['chunk_index'], passages=[
            dict(unit_ids=[unit['unit_id']], text=unit['text'].strip(), kind='verbatim_excerpt')
            for unit in supplied['source_units']], coverage_omissions=[])
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
        message=SimpleNamespace(content=json.dumps(body), refusal=None))])
client.chat.completions.create.side_effect = chat
engine.Transcriber = lambda **kw: Transcriber(client=client, **kw)
args = ['--workflow', '--interview', '--input', str(source), '--output-folder', 'interview-output',
        '--interviewer-name', 'SYNTHETIC_PRIVATE_HOST', '--interviewee-name', 'SYNTHETIC_PRIVATE_GUEST',
        '--audio-chunk-seconds', '1', '--speaker-map', '1:1:A=interviewer', '--chapters', 'both']
assert engine.entrypoint(args) == 0
out = Path('interview-output')
family = next(path.parent for path in (out / 'attributed').rglob('transcription.txt'))
provenance = json.loads((family / 'provenance.json').read_text())
assert [turn['role'] for turn in provenance['attribution']['turns']] == ['interviewer', None, None]
assert len(list(out.rglob('review_report.json'))) == 2
assert len(list(out.rglob('review_report.xlsx'))) == 2
assert len(list(out.rglob('chapter_drafts.json'))) == 2
assert len(list(out.rglob('derivative_readability.txt'))) == 2
original = next(path for path in out.iterdir() if (path / 'transcription.txt').is_file())
before = (original / 'transcription.txt').read_bytes()
calls = client.audio.transcriptions.create.call_count, client.chat.completions.create.call_count
assert interview(['transcribe', *args, '--resume']) == 0
assert calls == (client.audio.transcriptions.create.call_count, client.chat.completions.create.call_count)
assert before == (original / 'transcription.txt').read_bytes()
''')
    result = subprocess.run([str(python), str(script)], cwd=work, env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, 'Installed interview family smoke failed.'
    for private in ('SYNTHETIC_PRIVATE_', 'Synthetic words.', 'Original words.', str(work), 'interview-synthetic.wav'):
        assert private not in result.stdout + result.stderr
    assert result.stderr == ''
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert any(row.get('family') == 'attributed' and row.get('stage') == 'diarization' for row in events)
    executable = python.parent / ('voice-transcribe.exe' if os.name == 'nt' else 'voice-transcribe')
    result = subprocess.run([str(executable), '--input', 'SYNTHETIC_PRIVATE_PATH',
                             '--interviewer-name', 'SYNTHETIC_PRIVATE_NAME'],
                            cwd=work, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert 'SYNTHETIC_PRIVATE_' not in result.stdout + result.stderr
    assert 'Traceback' not in result.stdout + result.stderr


def test_wheel_native_batch_extensionless_and_unnamed_attribution(wheel_environment):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg/ffprobe required for synthetic installed staging.')
    work, python, environment, _ = wheel_environment
    work = work / 'batch-new-features'
    work.mkdir()
    script = work / 'batch-attribution.py'
    script.write_text('''
import contextlib, hashlib, io, json, runpy, socket, subprocess, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import voice_transcription_engine.cli as engine
from voice_transcription_engine.transcriber import Transcriber

def blocked(*a, **kw):
    raise AssertionError('No real network permitted.')
socket.socket.connect = blocked
source = Path('synthetic.mp4')
subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
    '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=10:d=1.125',
    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=1.125',
    '-c:v', 'mpeg4', str(source)], check=True, capture_output=True, timeout=30)
source = source.rename('SYNTHETIC_PRIVATE_FILENAME')
expected = hashlib.sha256(source.read_bytes()).hexdigest()
Path('interview.json').write_text(json.dumps({'version': 1, 'interview_id': 'SYNTHETIC_PRIVATE_SESSION',
    'parts': [{'id': 'recording', 'path': str(source), 'media_type': 'video'}]}))
Path('plan.json').write_text(json.dumps({'version': 1, 'interviews': [
    {'id': 'SYNTHETIC_PRIVATE_ENTRY', 'manifest': 'interview.json'}]}))
Path('speakers.json').write_text(json.dumps({'version': 1, 'interviews': [
    {'id': 'SYNTHETIC_PRIVATE_ENTRY', 'interviewee_name': 'SYNTHETIC_PRIVATE_GUEST',
     'speaker_map': ['1:1:B=interviewee']}]}))
client = MagicMock()
def audio(**kw):
    if kw['model'] != 'gpt-4o-transcribe-diarize':
        return SimpleNamespace(text='SYNTHETIC_PRIVATE_RAW.\\r\\nExact words.')
    return {'text': 'A: A question?\\nB: An answer.', 'segments': [
        {'speaker': 'A', 'text': 'A question?', 'start': 0.0, 'end': 0.5},
        {'speaker': 'B', 'text': 'An answer.', 'start': 0.5, 'end': 1.0}]}
def chat(**kw):
    p = json.loads(kw['messages'][-1]['content'])
    name = kw['response_format']['json_schema']['name']
    if name == 'faithful_transcript_edit':
        body = dict(chunk_index=p['chunk_index'], text=p['text'], speaker_uncertain=False)
    elif name == 'faithful_turn_group_edit':
        body = dict(group_index=p['group_index'], edits=[{**turn, 'speaker_uncertain': False} for turn in p['turns']])
    elif name == 'source_grounded_author_review':
        p['text'] = ''.join(p['text'] for p in p['evidence_pieces'])
        body = dict(chunk_index=p['chunk_index'], fully_reviewed=True,
            contract_version=p['contract_version'], reviewed_piece_ids=p['core_piece_ids'], findings=[])
    else:
        raise AssertionError('Deterministic interview chapters need no arrangement call.')
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
        message=SimpleNamespace(content=json.dumps(body), refusal=None))])
client.audio.transcriptions.create.side_effect = audio
client.chat.completions.create.side_effect = chat
engine.Transcriber = lambda **kw: Transcriber(client=client, **kw)
logs = []
executable = Path(sys.executable).parent / ('voice-batch.exe' if sys.platform == 'win32' else 'voice-batch')
def invoke(*args):
    sys.argv = ['voice-batch', *args]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        if sys.platform == 'win32':
            from voice_transcription_engine.batch.cli import main
            assert main(list(args)) == 0
        try:
            if sys.platform != 'win32':
                runpy.run_path(str(executable), run_name='__main__')
        except SystemExit as error:
            assert error.code == 0
    logs.append(output.getvalue())
original_open = Path.open
def guarded(path, *a, **kw):
    if path.absolute() == source.absolute():
        raise AssertionError('Metadata must not open video.')
    return original_open(path, *a, **kw)
Path.open = guarded
invoke('inventory', '--plan', 'plan.json')
invoke('check', '--plan', 'plan.json')
Path.open = original_open
invoke('prepare', '--plan', 'plan.json', '--batch', 'batch', '--copy-local-files')
invoke('verify', '--batch', 'batch')
flags = ['--batch', 'batch', '--send-to-openai', '--interview', '--speaker-config', 'speakers.json']
invoke('run', *flags, '--phase', 'raw')
assert client.audio.transcriptions.create.call_count == 2
invoke('run', *flags, '--phase', 'review')
reviews = {p: p.read_bytes() for p in Path('batch').rglob('review_report.*')}
invoke('run', *flags, '--phase', 'chapters', '--select', 'SYNTHETIC_PRIVATE_ENTRY',
       '--human-reviewed', '--chapters', 'interview')
assert client.audio.transcriptions.create.call_count == 2
assert all(p.read_bytes() == data for p, data in reviews.items())
assert len(list(Path('batch').rglob('chapter_drafts.json'))) == 2
provenance = json.loads(next(Path('batch/item-0001/output/attributed').rglob('provenance.json')).read_bytes())
assert [t['role'] for t in provenance['attribution']['turns']] == [None, 'interviewee']
assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
assert not source.with_suffix('.mp4').exists()
joined = ''.join(logs)
assert 'SYNTHETIC_PRIVATE' not in joined and str(Path.cwd()) not in joined
assert 'A question?' not in joined
''')
    result = subprocess.run([str(python), str(script)], cwd=work, env=environment,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, 'Installed native batch staging/attribution smoke failed.'
    assert result.stderr == ''


def test_wheel_recovery_checkpoint_controls_and_cli_privacy(wheel_environment):
    work, python, environment, _ = wheel_environment
    script = work / 'recovery-smoke.py'
    script.write_text('''
import io, json, shutil, socket, wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import voice_transcription_engine.cli as engine
import voice_transcription_engine.pipeline as pipeline
from voice_transcription_engine.transcriber import Transcriber
from voice_transcription_engine.provider_control import CURRENT_CONTROL
from voice_transcription_engine.studio_cli import main as interview

def blocked(*args, **kwargs):
    raise AssertionError('No external provider calls allowed.')
socket.socket.connect = blocked
root = Path.cwd() / 'recovery-installed'
root.mkdir()
source = root / 'SYNTHETIC_PRIVATE_SOURCE.wav'
with wave.open(str(source), 'wb') as wav:
    wav.setparams((1, 2, 100, 0, 'NONE', 'not compressed'))
    wav.writeframes(b'\\0\\0' * 250)
pipeline.prepare_audio = lambda source, target, **kwargs: shutil.copyfile(source, target)
provider = MagicMock()
provider.audio.transcriptions.create.return_value = SimpleNamespace(text='SYNTHETIC_PRIVATE_WORDS')
engine.Transcriber = lambda **kwargs: Transcriber(client=provider, **kwargs)
output = root / 'out'
args = ['--workflow', '--input', str(source), '--output-folder', str(output), '--stages', 'raw',
        '--audio-chunk-seconds', '1', '--provider-retries', '0', '--max-provider-requests', '1']
assert engine.entrypoint(args) == 1
assert provider.audio.transcriptions.create.call_count == 1
assert len(list(output.rglob('response.json'))) == 1
assert not list(output.rglob('transcription.txt'))
assert CURRENT_CONTROL.get() is None
args[-1] = '2'
assert interview(['transcribe', *args, '--resume']) == 0
assert provider.audio.transcriptions.create.call_count == 3
assert len(list(output.rglob('transcription.txt'))) == 1
logs = ''.join(path.read_text() for path in output.rglob('*.jsonl'))
assert 'SYNTHETIC_PRIVATE' not in logs and str(root) not in logs
assert 'request_limit' in logs
''')
    result = subprocess.run([str(python), str(script)], cwd=work, env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, 'Installed recovery smoke failed.'
    assert 'SYNTHETIC_PRIVATE' not in result.stdout + result.stderr
    for command in ('voice-transcribe', 'voice-batch'):
        executable = python.parent / (command + '.exe' if os.name == 'nt' else command)
        invalid = ['--max-provider-requests', 'SYNTHETIC_PRIVATE_VALUE']
        if command == 'voice-batch':
            invalid = ['run', *invalid]
        result = subprocess.run([str(executable), *invalid], cwd=work, env=environment,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 2
        assert 'SYNTHETIC_PRIVATE' not in result.stdout + result.stderr


def test_wheel_text_checkpoint_restart(wheel_environment):
    work, python, environment, _ = wheel_environment
    script = work / 'text-checkpoint-smoke.py'
    script.write_text('''
import json, socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from voice_transcription_engine.author_review import ReviewOptions, review_transcript

def blocked(*args, **kwargs):
    raise AssertionError('Offline only.')
socket.socket.connect = blocked
client = MagicMock()
raw = 'Synthetic exact source.\\n' * 12
root = Path('installed-text-checkpoints')
options = ReviewOptions(chunk_bytes=64)
def respond(**parameters):
    payload = json.loads(parameters['messages'][-1]['content'])
    body = dict(chunk_index=payload['chunk_index'], fully_reviewed=True,
                contract_version=payload['contract_version'], reviewed_piece_ids=payload['core_piece_ids'], findings=[])
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
        message=SimpleNamespace(content=json.dumps(body), refusal=None))])
def fail_late(**parameters):
    if json.loads(parameters['messages'][-1]['content'])['chunk_index'] == 2:
        raise RuntimeError('SYNTHETIC_PRIVATE_PROVIDER')
    return respond(**parameters)
client.chat.completions.create.side_effect = fail_late
assert review_transcript(raw, client, options, checkpoint_root=root)['status'] == 'incomplete'
calls = client.chat.completions.create.call_count
client.chat.completions.create.side_effect = respond
assert review_transcript(raw, client, options, checkpoint_root=root)['status'] == 'complete'
assert client.chat.completions.create.call_count == calls + 1
calls = client.chat.completions.create.call_count
assert review_transcript(raw, client, options, checkpoint_root=root)['status'] == 'complete'
assert client.chat.completions.create.call_count == calls
''')
    result = subprocess.run([str(python), str(script)], cwd=work, env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, 'Installed text checkpoint restart failed.'
    assert result.stdout == result.stderr == ''
