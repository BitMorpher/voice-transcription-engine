import openai
import os
import io
import math
import tempfile
from typing import List

from pydub import AudioSegment
from logger import get_logger

logger = get_logger(__name__)


class Transcriber:
    def __init__(self, model_name="whisper-1"):
        # Ensure OPENAI_API_KEY is set in environment
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY environment variable is required.")
        openai.api_key = api_key
        self.model_name = model_name
        # Maximum allowed file size per request (20 MiB)
        self.max_bytes = 20 * 1024 * 1024

    def _split_audio_into_chunks(self, audio_path: str) -> List[io.BytesIO]:
        """Split the input audio into chunks such that each chunk's exported size is <= max_bytes.

        Returns a list of BytesIO objects (ready for reading from the start). If splitting fails,
        raises an exception.
        """
        file_size = os.path.getsize(audio_path)
        if file_size <= self.max_bytes:
            raise ValueError("File does not need splitting")

        # Load audio with pydub
        audio = AudioSegment.from_file(audio_path)
        duration_ms = len(audio)
        if duration_ms <= 0:
            raise ValueError("Audio duration is zero")

        # Estimate bytes per ms based on the original file size
        bytes_per_ms = file_size / float(duration_ms)
        # Compute max chunk duration in ms so each chunk will be <= max_bytes
        max_ms_per_chunk = int(math.floor(self.max_bytes / bytes_per_ms))
        # Safeguard: at least 1 second
        if max_ms_per_chunk < 1000:
            max_ms_per_chunk = 1000

        chunks: List[io.BytesIO] = []
        # Determine export format from file extension
        _, ext = os.path.splitext(audio_path)
        ext = ext.lstrip('.').lower() or 'wav'

        for i in range(0, duration_ms, max_ms_per_chunk):
            chunk = audio[i:i + max_ms_per_chunk]
            bio = io.BytesIO()
            # pydub export will write encoded audio to the BytesIO
            chunk.export(bio, format=ext)
            bio.seek(0)
            # Give the BytesIO a name attribute so downstream libraries can infer filename
            bio.name = f"part_{i // max_ms_per_chunk}.{ext}"
            chunks.append(bio)

        return chunks

    def _split_audio_by_duration(self, audio_path: str, chunk_ms: int) -> List[str]:
        """Split the input .m4a audio into fixed-duration chunks (milliseconds).

        This writes each chunk to a temporary .m4a file and returns the list of file paths.
        Using real files is necessary for reliable exporting/encoding of .m4a with ffmpeg.
        """
        # Load audio with pydub (specify format to ensure correct demuxing)
        audio = AudioSegment.from_file(audio_path, format='m4a')
        duration_ms = len(audio)
        if duration_ms <= 0:
            raise ValueError("Audio duration is zero")

        temp_paths: List[str] = []
        # Export each chunk to a temporary .mp4 file
        for i, start in enumerate(range(0, duration_ms, chunk_ms)):
            end = start + chunk_ms
            chunk = audio[start:end]
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
            tmp_path = tmp.name
            tmp.close()
            # Export chunk to the temp file path using ffmpeg
            chunk.export(tmp_path, format='mp4')
            temp_paths.append(tmp_path)

        return temp_paths

    def transcribe(self, audio_path: str) -> str:
        """
        Transcribe the audio at audio_path using OpenAI's Whisper (via openai-python).
        If the file is larger than 20 MB it will be split into multiple chunks and each chunk
        will be transcribed separately. The final transcription is the concatenation of parts
        in order.
        Returns the verbatim transcription as a string.
        """
        client = openai.OpenAI()

        try:
            logger.info("Checking file size", {"audio_path": audio_path})
            file_size = os.path.getsize(audio_path)
            logger.info("File size checked", {"audio_path": audio_path, "size_bytes": file_size})
        except OSError as e:
            logger.error("Could not access file", {"audio_path": audio_path, "error": str(e)})
            raise FileNotFoundError(f"Could not access file '{audio_path}': {e}")

        # If file is small enough, transcribe directly
        logger.info("Starting transcription", {"audio_path": audio_path, "size_bytes": file_size})

        if file_size <= self.max_bytes:
            with open(audio_path, "rb") as audio_file:
                resp = client.audio.transcriptions.create(file=audio_file, model=self.model_name)
            text = getattr(resp, "text", "")
            logger.info("Finished transcription", {"audio_path": audio_path, "size_bytes": file_size, "transcript_length": len(text)})
            return text

        # Otherwise split into chunks and transcribe each
        _, ext = os.path.splitext(audio_path)
        ext = ext.lstrip('.').lower()
        try:
            if ext == 'm4a':
                # For m4a files, split by fixed duration (e.g., 5 minutes) to avoid encoding/size issues
                chunk_ms = 5 * 60 * 1000  # 5 minutes
                logger.info("Splitting .m4a by duration", {"audio_path": audio_path, "chunk_ms": chunk_ms})
                chunks = self._split_audio_by_duration(audio_path, chunk_ms)
            else:
                chunks = self._split_audio_into_chunks(audio_path)
        except Exception as exc:
            logger.warning("Failed to split audio, falling back to full-file transcription", {"audio_path": audio_path, "error": str(exc)})
            # Log traceback for debugging
            import traceback
            traceback.print_exc()
            # If splitting fails for any reason, fall back to transcribing the whole file
            with open(audio_path, "rb") as audio_file:
                resp = client.audio.transcriptions.create(file=audio_file, model=self.model_name)
            text = getattr(resp, "text", "")
            logger.info("Finished transcription (fallback)", {"audio_path": audio_path, "size_bytes": file_size, "transcript_length": len(text)})
            return text

        parts: List[str] = []
        logger.info("Transcribing chunks", {"audio_path": audio_path, "num_chunks": len(chunks)})
        for idx, chunk_item in enumerate(chunks):
            part_text = ""
            temp_file_to_delete = None
            try:
                if isinstance(chunk_item, str):
                    # chunk_item is a file path (from _split_audio_by_duration)
                    temp_file_to_delete = chunk_item
                    with open(chunk_item, 'rb') as fh:
                        resp = client.audio.transcriptions.create(file=fh, model=self.model_name)
                else:
                    # Assume file-like object
                    chunk_item.seek(0)
                    resp = client.audio.transcriptions.create(file=chunk_item, model=self.model_name)

                part_text = getattr(resp, "text", "")
                logger.info("Chunk transcribed", {"audio_path": audio_path, "chunk_index": idx, "part_length": len(part_text)})
            except Exception as exc:
                logger.error("Chunk transcription failed", {"audio_path": audio_path, "chunk_index": idx, "error": str(exc)})
                part_text = f"[transcription error on part {idx}: {exc}]"
            finally:
                # Cleanup a temp file if we created one earlier
                if temp_file_to_delete:
                    try:
                        os.remove(temp_file_to_delete)
                        logger.info("Removed temporary chunk file", {"temp_path": temp_file_to_delete})
                    except Exception:
                        logger.warning("Failed to remove temporary chunk file", {"temp_path": temp_file_to_delete})

            parts.append(part_text)

        combined = "\n\n".join(parts)
        logger.info("Finished transcription (chunks combined)", {"audio_path": audio_path, "size_bytes": file_size, "num_chunks": len(chunks), "transcript_length": len(combined)})
        return combined

    def enhance_transcription(self, transcription: str) -> str:
        """
        Improve readability: punctuation, capitalization, paragraphing while preserving meaning.
        """
        # ...existing readability enhancement code...
        prompt = (
            "You will receive a verbatim transcript. Return the same content edited for readability: "
            "add punctuation, capitalization, and paragraph breaks. Do not change meaning or add new information.\n\n"
            f"Transcript:\n{transcription}\n\nOutput:"
        )
        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=2048,
        )
        return getattr(resp.choices[0].message, "content", "").strip()

    def enhance_as_interview(self, transcription: str) -> str:
        """
        Use OpenAI to restructure the transcription as an interview between two people:
        "Interviewer" and "Interviewee". Keep the content and meaning verbatim (no added facts).
        Apply punctuation, short turns, and clear labels for each speaker.

        Returns the interview-formatted text.
        """
        prompt = (
            "You are an assistant that reformats verbatim transcripts into a clear interview "
            "between two people labeled 'Interviewer' and 'Interviewee'. Preserve the original "
            "content and meaning exactly—do not invent, omit, or add facts. Improve readability "
            "with punctuation, capitalization, and short paragraphs for each turn. Use the format:\n\n"
            "Interviewer: <question or prompt>\n"
            "Interviewee: <response>\n\n"
            "If speaker identity is unclear, assign turns logically but do not attribute words to a "
            "specific real person. Keep the tone neutral and faithful to the source.\n\n"
            f"Transcript:\n{transcription}\n\nFormatted interview:"
        )

        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=2048,
        )
        return getattr(resp.choices[0].message, "content", "").strip()