from setuptools import setup

with open('README.md', encoding='utf-8') as readme_file:
    long_description = readme_file.read()

setup(
    name='voice-transcription-engine',
    version='0.1.0',
    author='Your Name',
    author_email='your.email@example.com',
    description='A command-line interface for processing audio recordings using OpenAI\'s Whisper model to generate transcriptions.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/BitMorpher/voice-transcription-engine',
    py_modules=['cli', 'transcriber', 'media', 'pipeline', 'private_output', 'logger', 'utils'],
    package_dir={'': 'src'},
    install_requires=[
        'openai>=1.0',
    ],
    extras_require={'notebook': ['pydub']},
    entry_points={'console_scripts': ['voice-transcribe=cli:main']},
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.9',
)
