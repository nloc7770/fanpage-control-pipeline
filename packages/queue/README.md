# packages/queue

**Status:** placeholder. Owner: Phase-2 workers agent.

Celery config package, shared by the API (to enqueue tasks) and the workers
(to consume them). Must expose:

- `make_celery_app(name: str, settings) -> Celery` with broker/result from
  `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND`.
- `task_routes` mapping every known task name to its queue (see the table in
  `apps/workers/README.md`).
- A `register_signal_logging()` helper that hooks `task_prerun`, `task_postrun`,
  `task_failure` into loguru and into the `logs` table.
- A typed `enqueue(task_name, *args, **kwargs)` wrapper the API uses, so the
  API never imports Celery directly.

Keep it lean -- no task implementations here, only configuration.
