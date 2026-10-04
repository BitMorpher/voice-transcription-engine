"""The optional legacy notebook must work after removal of stdlib audioop."""

import pytest


def test_pydub_synthetic_m4a_roundtrip(synthetic_media, tmp_path):
    pydub = pytest.importorskip('pydub', reason='Install the notebook extra to test PyDub.')
    original = pydub.AudioSegment.from_file(synthetic_media('synthetic.m4a'), format='m4a')
    target = tmp_path / 'synthetic-chunk.m4a'
    with original[:500].export(target, format='mp4'):
        pass
    restored = pydub.AudioSegment.from_file(target, format='m4a')
    # Lossy AAC introduces codec padding; require the intended synthetic tone duration.
    assert 450 <= len(restored) <= 600
    assert restored.rms > 0
