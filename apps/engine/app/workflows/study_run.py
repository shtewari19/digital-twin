"""Workflow definitions for study run orchestration."""

from temporalio import workflow


@workflow.defn(name="study_run_workflow")
class StudyRunWorkflow:
    """Walking-skeleton workflow: proves API -> Temporal -> worker -> completion.

    Deliberately does nothing yet. Real SSR pipeline steps
    (reaction -> embed -> cosine -> shift -> normalize -> expected value ->
    penalty) get added here as activities in a follow-up ticket.
    """

    @workflow.run
    async def run(self, study_id: str) -> None:
        workflow.logger.info("study_run_workflow started study_id=%s", study_id)
        workflow.logger.info("study_run_workflow completed study_id=%s", study_id)
        return