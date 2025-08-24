import os


def validate_input_path(path):
    """Validate if the input path exists and is a directory."""
    if not os.path.exists(path):
        raise ValueError(f"The path {path} does not exist.")
    if not os.path.isdir(path):
        raise ValueError(f"The path {path} is not a directory.")

def validate_output_path(path):
    """Validate if the output path exists or can be created."""
    if not os.path.exists(path):
        os.makedirs(path)

def format_transcription(transcription):
    """Format the transcription for better readability."""
    return transcription.strip().replace('\n', ' ').replace('  ', ' ')