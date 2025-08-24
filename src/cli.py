import argparse
import os
import traceback
from transcriber import Transcriber

def main():
    parser = argparse.ArgumentParser(description="Process audio recordings and generate transcriptions.")
    parser.add_argument('--input_folder', type=str, required=True, help='Path to the folder containing audio files.')
    parser.add_argument('--output_folder', type=str, required=True, help='Path to the folder where transcriptions will be saved.')
    parser.add_argument('--enhance_for_reading', action='store_true', help='Enhance transcriptions for better readability.')
    parser.add_argument('--format_as_interview', action='store_true', help='Use OpenAI to format the transcription as an interview between two people (saved as *_enhanced_interview.txt).')

    args = parser.parse_args()

    if not os.path.exists(args.input_folder):
        print(f"Error: The input folder '{args.input_folder}' does not exist.")
        return

    if not os.path.exists(args.output_folder):
        os.makedirs(args.output_folder, exist_ok=True)

    transcriber = Transcriber()

    supported_ext = ('.wav', '.mp3', '.m4a')
    for audio_file in os.listdir(args.input_folder):
        if not audio_file.lower().endswith(supported_ext):
            continue

        audio_path = os.path.join(args.input_folder, audio_file)
        try:
            transcription = transcriber.transcribe(audio_path)
        except Exception as exc:
            print(f"Error transcribing '{audio_file}': {exc}")
            continue

        base_name = os.path.splitext(audio_file)[0]

        # Save verbatim transcription (conventional name: [original_filename]_transcription.txt)
        verbatim_filename = os.path.join(args.output_folder, f"{base_name}_transcription.txt")
        try:
            with open(verbatim_filename, 'w', encoding='utf-8') as f:
                f.write(transcription)
            print(f"Verbatim transcription saved to '{verbatim_filename}'.")
        except Exception as exc:
            print(f"Error saving verbatim transcription for '{audio_file}': {exc}")

        # Optional: readability-enhanced version
        if args.enhance_for_reading:
            try:
                enhanced = transcriber.enhance_transcription(transcription)
                enhanced_filename = os.path.join(args.output_folder, f"{base_name}_enhanced.txt")
                with open(enhanced_filename, 'w', encoding='utf-8') as f:
                    f.write(enhanced)
                print(f"Enhanced (readability) transcription saved to '{enhanced_filename}'.")
            except Exception as exc:
                print(f"Error creating enhanced (readability) transcription for '{audio_file}': {exc}")
                traceback.print_exc()

        # Optional: OpenAI interview-formatted version
        if args.format_as_interview:
            try:
                interview_text = transcriber.enhance_as_interview(transcription)
                interview_filename = os.path.join(args.output_folder, f"{base_name}_enhanced_interview.txt")
                with open(interview_filename, 'w', encoding='utf-8') as f:
                    f.write(interview_text)
                print(f"Interview-formatted transcription saved to '{interview_filename}'.")
            except Exception as exc:
                print(f"Error creating interview-formatted transcription for '{audio_file}': {exc}")
                # Print traceback
                traceback.print_exc()

if __name__ == "__main__":
    main()