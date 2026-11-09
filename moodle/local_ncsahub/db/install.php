<?php
defined('MOODLE_INTERNAL') || die();

/**
 * One-time backfill: every past course completion goes into the outbox (no log tables are read).
 */
function xmldb_local_ncsahub_install() {
    global $DB;
    $now = time();
    $rs = $DB->get_recordset_select('course_completions', 'timecompleted > 0', null, 'timecompleted, id',
        'id, userid, course, timecompleted');
    $batch = [];
    foreach ($rs as $c) {
        $batch[] = (object)['kind' => 'course_completed', 'userid' => $c->userid, 'courseid' => $c->course,
            'cmid' => 0, 'state' => 1, 'timecreated' => $c->timecompleted, 'timequeued' => $now];
        if (count($batch) >= 1000) {
            $DB->insert_records('local_ncsahub_outbox', $batch);
            $batch = [];
        }
    }
    $rs->close();
    if ($batch) {
        $DB->insert_records('local_ncsahub_outbox', $batch);
    }
    return true;
}
