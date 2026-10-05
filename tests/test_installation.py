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
        assert 'voice_transcription_engine/batch/runner.py' in names
        assert any(name.startswith('voice_transcription_engine/prompts/') and name.endswith('.txt') for name in names)
        assert 'cli.py' not in names
        assert not any('private/' in name or '.env' in name or 'batch-plan' in name for name in names)
    result = subprocess.run([str(python), '-c',
        'import voice_transcription_engine.cli as c; print(c.__file__)'],
        cwd=work, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert str(work / 'runtime') in result.stdout
    for command in ('voice-transcribe', 'voice-batch'):
        executable = python.parent / (command + '.exe' if os.name == 'nt' else command)
        result = subprocess.run([str(executable), '--help'], cwd=work, env=environment,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and command in result.stdout
        assert result.stderr == ''


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
    elif name == 'source_grounded_author_review':
        body = dict(chunk_index=p['chunk_index'], fully_reviewed=True, reviewed_start=p['core_start'],
                    reviewed_end=p['core_end'], findings=[])
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
assert batch(args) == 0
assert client.chat.completions.create.call_count == calls
assert client.audio.transcriptions.create.call_count == 1
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
    elif name == 'source_grounded_author_review':
        body = dict(chunk_index=supplied['chunk_index'], fully_reviewed=True,
                    reviewed_start=supplied['core_start'], reviewed_end=supplied['core_end'], findings=[])
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
assert engine.entrypoint(args + ['--resume']) == 0
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
