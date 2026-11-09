<?php
namespace local_ncsahub\task;

defined('MOODLE_INTERNAL') || die();

/**
 * Delete outbox rows queued more than 30 days ago (the front door pulls hourly).
 */
class prune_outbox extends \core\task\scheduled_task {
    public function get_name(): string {
        return get_string('task_prune', 'local_ncsahub');
    }

    public function execute(): void {
        global $DB;
        $DB->delete_records_select('local_ncsahub_outbox', 'timequeued < ?', [time() - 30 * DAYSECS]);
    }
}
