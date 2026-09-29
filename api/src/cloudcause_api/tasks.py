"""Cloud Tasks dispatch for live investigations.

Only active when ``CLOUDCAUSE_DISPATCH_MODE=cloud_tasks``. The default
``background`` mode (FastAPI ``BackgroundTasks``) needs none of this and is
what local dev, CI, and docker-compose run; this path is unverified against a
real GCP project.
"""

from __future__ import annotations

from cloudcause_contracts import Settings


class CloudTasksNotConfiguredError(RuntimeError):
    """CLOUDCAUSE_DISPATCH_MODE=cloud_tasks is missing a required setting."""


class CloudTasksDispatcher:
    """Enqueues one idempotently-named claim task per live investigation."""

    def __init__(self, settings: Settings) -> None:
        missing = [
            name
            for name, value in (
                ("CLOUDCAUSE_TASKS_PROJECT_ID", settings.tasks_project_id),
                ("CLOUDCAUSE_TASKS_LOCATION", settings.tasks_location),
                ("CLOUDCAUSE_TASKS_QUEUE", settings.tasks_queue),
                ("CLOUDCAUSE_TASKS_WORKER_URL", settings.tasks_worker_url),
            )
            if not value
        ]
        if missing:
            raise CloudTasksNotConfiguredError(
                "CLOUDCAUSE_DISPATCH_MODE=cloud_tasks requires " + ", ".join(missing)
            )
        if settings.history_backend == "memory":
            # The claim task is delivered over HTTP to the worker URL, which in
            # any real cloud_tasks deployment is a different process from the
            # one that created the job. An in-process job store is invisible
            # there, so every delivery would 404 and no live investigation
            # would ever run.
            raise CloudTasksNotConfiguredError(
                "CLOUDCAUSE_DISPATCH_MODE=cloud_tasks requires a durable job store: "
                "set CLOUDCAUSE_HISTORY_BACKEND=postgres, and point the gateway and "
                "the claim target at the same database"
            )
        self._settings = settings
        self._client = None  # constructed lazily so importing this module never requires the SDK

    def _get_client(self):  # type: ignore[no-untyped-def]
        if self._client is None:
            from google.cloud import tasks_v2  # imported lazily: only cloud_tasks dispatch needs it

            self._client = tasks_v2.CloudTasksAsyncClient()
        return self._client

    async def enqueue_claim(self, investigation_id: str) -> None:
        """Enqueue the private worker's claim endpoint for one investigation.

        The task name is derived from ``investigation_id``, so a duplicate
        enqueue (e.g. a retried gateway request) is a Cloud Tasks no-op rather
        than a second delivery.
        """

        from google.cloud import tasks_v2

        client = self._get_client()
        parent = client.queue_path(
            self._settings.tasks_project_id,
            self._settings.tasks_location,
            self._settings.tasks_queue,
        )
        url = f"{self._settings.tasks_worker_url.rstrip('/')}/internal/investigations/{investigation_id}/claim"
        http_request = tasks_v2.HttpRequest(url=url, http_method=tasks_v2.HttpMethod.POST)
        if self._settings.tasks_service_account:
            http_request.oidc_token = tasks_v2.OidcToken(
                service_account_email=self._settings.tasks_service_account
            )
        task = tasks_v2.Task(
            name=client.task_path(
                self._settings.tasks_project_id,
                self._settings.tasks_location,
                self._settings.tasks_queue,
                investigation_id,
            ),
            http_request=http_request,
        )
        await client.create_task(parent=parent, task=task)
