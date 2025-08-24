import openai
import os


class Transcriber:
    def __init__(self, model_name="whisper-1"):
        # Ensure OPENAI_API_KEY is set in environment
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY environment variable is required.")
        openai.api_key = api_key
        self.model_name = model_name
        # ...existing initialization code...

    def transcribe(self, audio_path: str) -> str:
        """
        Transcribe the audio at audio_path using OpenAI's Whisper (via openai-python).
        Returns the verbatim transcription as a string.
        """
        # from openai import OpenAI
        client = openai.OpenAI()
        with open(audio_path, "rb") as audio_file:
            resp = client.audio.transcriptions.create(file=audio_file, model=self.model_name)
        return getattr(resp, "text", "")

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