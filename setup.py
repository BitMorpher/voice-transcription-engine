from setuptools import setup, find_packages

setup(
    name='voice-transcription-engine',
    version='0.1.0',
    author='Your Name',
    author_email='your.email@example.com',
    description='A command-line interface for processing audio recordings using OpenAI\'s Whisper model to generate transcriptions.',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    url='https://github.com/yourusername/voice-transcription-engine',
    packages=find_packages(where='src'),
    package_dir={'': 'src'},
    install_requires=[
        'openai',
        'argparse',  # or 'click' if you choose to use Click for CLI
        'pydub',     # for audio file handling
        'numpy',     # for numerical operations if needed
        'scipy',     # for additional audio processing if needed
    ],
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.6',
)