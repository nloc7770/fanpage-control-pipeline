# services/whisperx

**Status:** placeholder. Owner: Phase-2 workers agent.

WhisperX-based ASR. One task: `asr.transcribe`.

Responsibilities:

- Load WhisperX model (`WHISPERX_MODEL`, `WHISPERX_DEVICE`,
  `WHISPERX_COMPUTE_TYPE`, `WHISPERX_BATCH_SIZE`).
- Transcribe + force-align to word level.
- Write a `transcripts` row (one per job) with `language`, `segments`, `words`.
- Persist a `transcript_json` asset for downstream debugging.
- On `MOCK_ASR=1`, return a bundled fixture transcript.

Emits `job.progress` (stage=`transcribing`). On completion, enqueues
`diarization.diarize` if `ENABLE_DIARIZATION=1`, else `vision.detect_objects`.
