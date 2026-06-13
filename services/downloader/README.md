# services/downloader

**Status:** placeholder. Owner: Phase-2 workers agent.

Wraps yt-dlp. One task: `download.fetch_source`.

Responsibilities:

- Resolve and download the source video (`DOWNLOAD_FORMAT`).
- Extract `title`, `duration`, `thumbnail_url`, `uploader`, `upload_date`,
  `view_count` into `jobs.source_metadata`.
- Persist `source_video` and `source_thumbnail` assets via the storage backend.
- Honor `DOWNLOAD_COOKIES_FILE` if set.
- On `MOCK_DOWNLOAD=1`, copy a bundled fixture mp4 from `tests/fixtures/`.

Emits `job.progress` (stage=`downloading`) every ~1s using yt-dlp's progress
hook. On completion, enqueues `asr.transcribe`.
