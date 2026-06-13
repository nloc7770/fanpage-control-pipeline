# services/diarization

**Status:** placeholder. Owner: Phase-2 workers agent.

pyannote-based speaker diarization. One task: `diarization.diarize`.

Responsibilities:

- Authenticate with `HUGGINGFACE_TOKEN`.
- Run pyannote.audio speaker diarization pipeline on `source_audio`.
- Write one `speakers` row per detected speaker, `timeline` = list of
  `{start, end}` segments.
- Persist `diarization_json` asset.
- On `MOCK_DIAR=1` (alias `ENABLE_DIARIZATION=0`), skip and pass through.

Emits `job.progress` (stage=`analyzing`). On completion, enqueues
`vision.detect_objects`.
